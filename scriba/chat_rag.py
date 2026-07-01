"""Self-ask RAG do chat (#22): a IA decide O QUE buscar na transcrição, o FTS local acha,
a IA responde só com os trechos. A transcrição INTEIRA nunca entra num prompt — só os
trechos que a busca devolve.

Fluxo por pergunta:
  1. plan_queries() — chamada de IA #1: dado o RESUMO + pergunta, a IA devolve as buscas
     (termos/sinônimos) ou "SEM_BUSCA" se o resumo já basta. Curta e barata.
  2. TranscriptSearcher.search() — LOCAL, sem IA: FTS5 :memory: sobre a transcrição.
  3. answer() — chamada de IA #2: responde usando resumo + trechos recuperados.

Tudo via ai.complete → funciona com qualquer provider (claude CLI / Ollama / OpenAI-compat).
"""

from __future__ import annotations

import re

from . import ai

_MAX_TURNS = 50  # teto de segurança do histórico (acumula até aqui; igual chat_ui). O /clear zera.

_PLANNER_SYSTEM = (
    "Você prepara buscas para responder a uma pergunta sobre uma reunião. Recebe o RESUMO "
    "da reunião e a pergunta; a transcrição completa NÃO está aqui, mas existe uma busca por "
    "palavra-chave (FTS) sobre ela. Sua ÚNICA tarefa é decidir o que buscar:\n"
    "- Se o RESUMO já basta para responder com segurança, escreva exatamente: SEM_BUSCA\n"
    "- Senão, liste de 1 a 4 buscas que achariam os trechos na transcrição. Como a busca é "
    "por palavra-chave, inclua SINÔNIMOS e variações (nomes próprios, números, o jeito falado). "
    "Uma busca por linha, só os termos — sem numeração, sem aspas, sem explicação."
)

_ANSWER_SYSTEM = (
    "Você responde perguntas sobre uma reunião, em português, de forma direta e fiel ao "
    "material. O material é o RESUMO e, quando houver, TRECHOS da transcrição recuperados por "
    "busca (podem vir fora de ordem ou incompletos) — baseie-se só nele. Cite o carimbo "
    "[HH:MM:SS] quando ajudar a localizar. Se a resposta não estiver no material, diga que não "
    "encontrou na reunião e sugira reformular a pergunta; não invente."
)

_NO_SEARCH = "SEM_BUSCA"


def _history_block(history) -> list[str]:
    out = []
    for pq, pa in (history or [])[-_MAX_TURNS:]:
        out.append(f"Pergunta anterior: {pq}\nResposta: {pa}")
    return out


def parse_queries(text: str) -> list[str]:
    """Extrai as buscas da resposta do planner. [] quando ele diz SEM_BUSCA (em qualquer
    linha — a IA às vezes explica junto). Limpa marcadores/numeração; no máx. 4 buscas."""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if any(_NO_SEARCH in ln.upper().replace(" ", "") for ln in lines):
        return []
    out: list[str] = []
    for ln in lines:
        ln = re.sub(r"^\s*[-*•]\s*", "", ln)          # bullets
        ln = re.sub(r"^\s*\d+[.)]\s*", "", ln)              # "1. " / "1) "
        ln = re.sub(r"(?i)^(buscas?|queries?|termos?)\s*:\s*", "", ln)  # rótulo
        ln = ln.strip().strip('"')
        if ln:
            out.append(ln)
    return out[:4]


def plan_queries(summary, history, question, *, timeout, model) -> list[str] | None:
    """Chamada #1. Devolve as buscas (ou [] para SEM_BUSCA). None = a IA falhou (o chamador
    decide o fallback)."""
    parts = ["RESUMO DA REUNIÃO:", (summary or "").strip() or "(sem resumo)", ""]
    parts += _history_block(history)
    parts.append(f"PERGUNTA ATUAL: {question}")
    out = ai.complete(_PLANNER_SYSTEM, "\n\n".join(parts), timeout=timeout, hidden_window=True, model=model)
    if out is None:
        return None
    return parse_queries(out)


def answer(summary, snippets, history, question, *, timeout, model) -> str | None:
    """Chamada #2. Responde usando o resumo + os trechos recuperados (a transcrição inteira
    nunca vai aqui)."""
    parts = ["RESUMO DA REUNIÃO:", (summary or "").strip() or "(sem resumo)"]
    if snippets:
        parts.append("")
        parts.append("## Trechos da transcrição (recuperados por busca)")
        parts.append("\n\n".join(f"[trecho {i + 1}]\n{s}" for i, s in enumerate(snippets)))
    else:
        parts.append("")
        parts.append("(A busca não retornou trechos da transcrição para esta pergunta.)")
    parts.append("")
    for pq, pa in (history or [])[-_MAX_TURNS:]:
        parts.append(f"Pergunta: {pq}\nResposta: {pa}")
    parts.append(f"Pergunta: {question}\nResposta:")
    return ai.complete(_ANSWER_SYSTEM, "\n\n".join(parts), timeout=timeout, hidden_window=True, model=model)


def respond(summary, history, question, *, searcher, timeout, model, status=None) -> str | None:
    """Orquestra o self-ask: planeja as buscas, recupera os trechos e responde.

    `searcher` é um TranscriptSearcher (ou None se a nota não tem transcrição). `status` é um
    callback opcional (str) para dar feedback na UI entre as etapas. Retorna o texto da
    resposta, ou None se a IA falhar na etapa final.
    """
    def note(msg: str) -> None:
        if status:
            status(msg)

    note("planejando a busca…")
    queries = plan_queries(summary, history, question, timeout=timeout, model=model)
    if queries is None:
        queries = [question]  # planner falhou: busca direto pela pergunta (resiliência)

    snippets: list[str] = []
    if queries and searcher is not None:
        note("buscando na transcrição…")
        snippets = searcher.search(queries)

    note("redigindo a resposta…")
    return answer(summary, snippets, history, question, timeout=timeout, model=model)
