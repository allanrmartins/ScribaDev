"""Geração do notas.md: resumo estruturado via claude -p + transcrição completa."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import merge as merge_mod
from . import util
from .config import load

SYSTEM_PROMPT = """\
Você é um gerador de registros técnicos a partir de transcrições automáticas de \
reuniões (Microsoft Teams/Zoom) sobre trabalho SAP/ABAP, em português do Brasil. \
Sua saída é consumida por uma IA (ex.: Claude Code) como CONTEXTO para continuar a \
atividade tratada — implementar, analisar, depurar, estimar ou responder — sem acesso à \
reunião e sem poder fazer perguntas. Por isso você escreve de forma densa, factual e \
autossuficiente, e produz EXCLUSIVAMENTE o documento em markdown no formato pedido. \
Você nunca responde de forma conversacional, nunca pede informações adicionais, nunca \
oferece ajuda extra e não usa ferramentas. Mesmo que a transcrição esteja curta, \
fragmentada ou com erros, você produz o melhor registro possível com o que houver, sem \
comentar sobre a qualidade do material.\
"""

DEFAULT_SUMMARY_PROMPT = """\
A seguir está a transcrição com timestamps de uma reunião (Teams/Zoom) sobre trabalho \
SAP/ABAP, em português do Brasil. Falas marcadas como "Eu" são do desenvolvedor ABAP; \
falas marcadas como "Participantes" — ou "Participante 1", "Participante 2"… quando a \
separação por voz está ativa — são de analistas funcionais, clientes ou outros desenvolvedores.

A reunião pode ser sobre QUALQUER atividade do dia a dia de um desenvolvedor ABAP: \
desenvolvimento novo, mudança em programa existente, análise/debug (inclusive de código \
standard, ajudando um funcional), estimativa de esforço, suporte/dúvida técnica, ajuda a \
outro desenvolvedor, incidente de produção, revisão de código, alinhamento de projeto. \
NÃO assuma que é um desenvolvimento: identifique a atividade real pelo conteúdo da conversa.

Este documento será usado como CONTEXTO por uma IA (ex.: Claude Code) para continuar o \
trabalho — implementar, analisar, depurar, estimar ou responder — SEM acesso à reunião e \
SEM poder fazer perguntas. Escreva de forma densa, factual e autossuficiente: cada item \
precisa fazer sentido sozinho, sem depender de ler a transcrição. Sem frases de cortesia, \
sem narrativa do tipo "foi discutido que" ou "o cliente falou que" — vá direto ao fato \
técnico acionável.

Gere APENAS markdown, sem preâmbulo, sem cercas de código e sem nenhum texto fora das \
seções abaixo, com exatamente estas seções nesta ordem:

## Objetivo
(PRIMEIRA linha: `**Tipo:** <atividade>`, onde <atividade> é UMA de: desenvolvimento | \
mudança em existente | análise/debug | estimativa | suporte/dúvida | incidente | \
revisão de código | alinhamento. Depois, 1 a 3 frases diretas dizendo o que precisa ser \
feito — ou o que foi feito, se a call já resolveu. Ex.: "**Tipo:** análise/debug" + \
"Identificar por que a remessa não gera fatura quando…")

## Contexto
(módulo(s) SAP — MM, SD, FI, CO, PP…; sistema/release quando citado — ECC, S/4HANA; \
ambiente/mandante; e a motivação de negócio em 1 frase. Só fatos, sem rodeios.)

## Detalhamento
(a seção principal — MOLDE o conteúdo ao Tipo identificado:
- **desenvolvimento / mudança em existente**: especificação funcional ACIONÁVEL no \
imperativo ("Selecionar…", "Validar…"): tipo de objeto (relatório ALV, transação Z, \
BAPI/RFC, BAdI/user exit/enhancement, CDS view, app RAP/Fiori, formulário, interface/IDoc, \
job), entrada/tela de seleção, fontes de dados no formato TABELA-CAMPO (ex.: MARA-MATNR), \
processamento passo a passo, validações (condição → mensagem), saída/layout, autorizações \
e volumes. Termine com a subseção `### Critérios de aceite` (checklist "- [ ] …" verificável);
- **análise/debug / incidente**: sintoma exato, passos para reproduzir, o que JÁ foi \
verificado e o resultado de cada verificação, hipóteses levantadas, próximos pontos de \
investigação (transação, programa, ponto de debug);
- **estimativa**: escopo e entregáveis, premissas assumidas, dependências, riscos e \
fatores de complexidade citados — os insumos que uma estimativa de esforço precisa;
- **suporte / dúvida / ajuda**: o que foi perguntado, a explicação ou solução dada \
(passo a passo, reproduzível), e o que ficou de ser verificado depois.
Cite [HH:MM:SS] nos pontos que possam precisar ser rastreados até a fala de origem.)

## Regras de negócio
(TODAS as regras de negócio, algoritmos e definições funcionais ditas na reunião — mesmo \
as que não fazem parte direta da tarefa. Numere RN-01, RN-02…; cada regra AUTOSSUFICIENTE. \
Quando for um algoritmo ou fluxo, escreva os passos na ordem: "RN-01: Para achar o item \
de custo — 1. Ler BSEG pelo nº do documento (BELNR); 2. Com BSEG-AUFNR, buscar a ordem \
na AUFK; 3. …". Capture também explicações de funcionamento do sistema ("a fatura só é \
gerada quando…"). Cite [HH:MM:SS]. Se nada foi dito, "Nada identificado.")

## Objetos SAP citados
(tabela markdown, um objeto por linha, EXATAMENTE com este cabeçalho e separador:

| Tipo | Objeto | Observação | Quando |
|---|---|---|---|

Tipos: transação, tabela, campo, programa, classe, função/BAPI, BAdI/user exit, CDS view, \
serviço OData, formulário, job, IDoc. "Quando" é o timestamp [HH:MM:SS] da primeira menção. \
Se nenhum objeto foi citado, escreva "Nada identificado." no lugar da tabela.)

## Decisões
(lista; cada decisão AUTOSSUFICIENTE — o que foi decidido E a razão, quando dada — para não \
depender da transcrição. Cite [HH:MM:SS]. Ex.: "Buscar direto de MSEG em vez de MKPF+MSEG \
para reduzir o custo do join — volume alto na produção [00:14:20].")

## Pendências e Ações
(o que ficou em ABERTO: dúvidas não resolvidas, definições faltando e tarefas com responsável \
quando citado; cite [HH:MM:SS]. Esta seção avisa a IA o que NÃO está definido — seja explícito \
sobre cada ambiguidade para que ela sinalize em vez de presumir.)

## Participantes
(Liste só quem a transcrição evidencia. Separe quem ESTAVA na call de quem foi só CITADO — nunca misture os dois:
- **Presentes** — "Eu" (SEMPRE o desenvolvedor ABAP que gravou a call) e cada "Participante N" da diarização. Cada número é uma voz DISTINTA e consistente do início ao fim — não funda duas vozes nem invente participantes além dos que aparecem. Só dê um NOME a um "Participante N" quando ficar claro que AQUELA VOZ é a pessoa: ela se identifica ("aqui é o Marcelo") ou é chamada pelo nome e responde em seguida. Se a pessoa aparece só em 3ª pessoa (falam SOBRE ela), ela NÃO é essa voz — mantenha "Participante N" sem nome. Na dúvida, deixe sem nome.
- **Mencionados** — pessoas citadas que NÃO são vozes da call (clientes, terceiros, colegas ausentes), com o papel quando dado; deixe explícito que não necessariamente estavam presentes.
Regra de ouro: nunca rotule uma voz presente com o nome de alguém que estava sendo apenas discutido.)

Regras:
- não invente NADA que não esteja na transcrição; se o nome de um objeto estiver incerto, \
marque com (?);
- ao atribuir uma fala, decisão ou ação, use "Eu" ou "Participante N" para quem ESTAVA na call e o nome só para quem foi apenas citado (deixando claro que é externo) — nunca troque os dois (ver Participantes);
- normalize termos SAP corrompidos pelo reconhecimento de voz: junte soletrações \
("S E dezesseis N" → SE16N, "vê a zero um" → VA01), use maiúsculas em transações, tabelas \
e campos (se16n → SE16N, mara → MARA), "bapi" → BAPI, "badi" → BAdI, "fiori" → Fiori;
- preserve nomes técnicos exatamente como são (MATNR, BUKRS, objetos Z*), sem traduzi-los;
- a transcrição é automática e pode conter erros — corrija apenas grafia óbvia de termos \
SAP, nunca o sentido do que foi dito;
- prefira precisão a completude: um item curto e claro vale mais que um parágrafo vago;
- se uma seção não tiver conteúdo, escreva "Nada identificado.".
"""


# Enquadramento determinístico (gerado pelo código, não pela IA) inserido no topo da nota,
# logo após o título: diz à IA consumidora o que é o documento e como usá-lo.
AI_CONTEXT_NOTE = (
    "> **Contexto para IA:** registro técnico de uma reunião SAP/ABAP, derivado de "
    "transcrição. **Objetivo** declara o tipo de atividade (desenvolvimento, análise/debug, "
    "estimativa, suporte…) e o que fazer — execute ESSA atividade, não presuma que é um "
    "desenvolvimento. **Detalhamento** e **Regras de negócio** são a fonte da verdade; "
    "**Pendências e Ações** lista o que ainda não está definido — sinalize essas lacunas em "
    "vez de presumir. A *Transcrição completa* ao final é apenas backup de rastreabilidade."
)


def ensure_prompt_file() -> Path:
    """Garante o prompt.md editável (criado com o padrão na primeira vez)."""
    util.ensure_app_dirs()
    if not util.PROMPT_PATH.exists():
        util.atomic_write_text(util.PROMPT_PATH, DEFAULT_SUMMARY_PROMPT)
    return util.PROMPT_PATH


def load_summary_prompt() -> str:
    """Instruções do resumo: prompt.md do usuário, ou o padrão se vazio/ausente."""
    try:
        text = util.PROMPT_PATH.read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    return DEFAULT_SUMMARY_PROMPT


def ensure_context_file() -> Path:
    """Garante o context.md editável (criado com o padrão na primeira vez)."""
    util.ensure_app_dirs()
    if not util.CONTEXT_PATH.exists():
        util.atomic_write_text(util.CONTEXT_PATH, AI_CONTEXT_NOTE)
    return util.CONTEXT_PATH


def load_context_note() -> str:
    """Cabeçalho 'Contexto para IA' editável (context.md). Arquivo AUSENTE → padrão
    (AI_CONTEXT_NOTE); arquivo VAZIO → '' (o usuário removeu o cabeçalho de propósito)."""
    try:
        return util.CONTEXT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return AI_CONTEXT_NOTE


# Pedida pelo código (fora do prompt.md editável) para garantir título e cliente
# mesmo que o usuário personalize as instruções da ata.
TITLE_INSTRUCTION = (
    "Na PRIMEIRA linha da sua resposta, escreva exatamente `TITULO: ` seguido de um "
    "título curto (3 a 6 palavras) que resuma o assunto principal da reunião — sem aspas, "
    "sem ponto final, nada além disso na linha. "
    "Na SEGUNDA linha, escreva exatamente `CLIENTE: ` seguido do nome do cliente a que a "
    "reunião se refere — a empresa dona do sistema/projeto/chamado discutido (não a "
    "consultoria que presta o serviço, e não o fornecedor de software). Se a transcrição "
    "não permitir identificar o cliente com confiança, escreva exatamente `CLIENTE: ?`. "
    "Se um NOME DA REUNIÃO (do título da janela) for fornecido adiante, use-o como pista "
    "forte para o TÍTULO e o CLIENTE — mas a transcrição prevalece se houver conflito. "
    "A partir da linha seguinte, siga as instruções abaixo normalmente.\n\n"
)

# respostas do modelo para "cliente não identificado" que viram campo vazio
_NO_CLIENT = {"?", "", "n/a", "na", "nenhum", "não identificado", "nao identificado",
              "desconhecido", "indefinido"}


# Linhas que um modelo às vezes emite ANTES do header pedido (apesar do
# SYSTEM_PROMPT) e que podemos pular com segurança ao procurar TITULO:/CLIENTE:.
_FENCE_RE = re.compile(r"^```")
_PREAMBLE_RE = re.compile(
    r"^(aqui (está|esta)|segue( o| a)?|claro|com certeza|perfeito|entendi)\b",
    re.IGNORECASE,
)


def _skippable_preamble(stripped: str) -> bool:
    """`stripped` (linha não-vazia) é cerca de código ou abertura conversacional
    conhecida — algo que pode preceder o header sem ser conteúdo da nota?"""
    return bool(_FENCE_RE.match(stripped) or _PREAMBLE_RE.match(stripped))


def split_header(text: str) -> tuple[str, str | None, str | None]:
    """Separa as linhas `TITULO:`/`CLIENTE:` do início da resposta do modelo.

    Tolera preâmbulo ANTES do header — linhas em branco, cercas de código
    (```` ```markdown ````) e aberturas conversacionais ("Aqui está:") — mas PARA
    no primeiro conteúdo substantivo: um `TITULO:`/`CLIENTE:` no meio do corpo
    (ex.: citação da transcrição) NÃO é consumido como header.

    Retorna (resumo, título|None, cliente|None) — `CLIENTE: ?` vira None.
    """
    lines = text.split("\n")
    title = client = None
    last_header = -1          # índice da última linha consumida como header
    skip_budget = 4           # nº de linhas de preâmbulo/cerca toleradas antes do header
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "":     # linhas em branco nunca contam como conteúdo
            i += 1
            continue
        up = stripped.upper()
        if up.startswith("TITULO:") and title is None:
            title = stripped.split(":", 1)[1].strip().strip('"').strip() or None
            last_header = i
            i += 1
            continue
        if up.startswith("CLIENTE:") and client is None:
            c = stripped.split(":", 1)[1].strip().strip('"').strip()
            client = None if c.lower() in _NO_CLIENT else c
            last_header = i
            i += 1
            continue
        # não é header: só seguimos varrendo se ainda não vimos NENHUM header e a
        # linha é um preâmbulo conhecido (orçamento limitado p/ não virar promíscuo).
        if last_header < 0 and skip_budget > 0 and _skippable_preamble(stripped):
            skip_budget -= 1
            i += 1
            continue
        break  # conteúdo substantivo → encerra a busca por header
    if last_header < 0:
        return text, None, None
    return "\n".join(lines[last_header + 1:]).strip(), title, client


# Timeout do resumo ESCALADO pela duração da call: o resumo é geração-bound e cresce
# com o tamanho da reunião, então um valor fixo cortaria o resumo de uma call longa
# (ex.: 4 h). `[summary].timeout_seconds` vira o PISO; escala N s por minuto de áudio,
# com um teto de segurança (evita travar o worker se o claude pendurar de vez).
_SUMMARY_S_PER_AUDIO_MIN = 20
_SUMMARY_TIMEOUT_CEILING = 3600  # 1 h — folga grande mesmo p/ uma call de 4 h


def generate_summary(transcript_md: str, folder: Path) -> tuple[str | None, str | None, str | None]:
    """Gera o resumo estruturado da transcrição via o provider de IA configurado.

    Retorna (resumo, título, cliente) — None em cada campo se a IA estiver
    desligada ou falhar. O provider (claude CLI / Ollama / OpenAI-compatível) é
    escolhido em [summary].provider; ver scriba/ai.py.
    """
    cfg = load().summary
    if not cfg.enabled:
        return None, None, None
    from . import ai

    # Instruções + transcrição vão juntas no payload (stdin no claude CLI; mensagem
    # 'user' nos providers HTTP). cwd=folder mantém o claude fora de qualquer projeto
    # (sem CLAUDE.md alheio no contexto); hidden_window=False de propósito — isto roda
    # no worker (console já oculto) e forçar CREATE_NO_WINDOW dispararia o bug do
    # Windows Terminal (ver o caminho da bandeja em promptgen._call_claude).
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    meeting = (meta.get("meeting_title") or "").strip()

    payload = f"{TITLE_INSTRUCTION}{load_summary_prompt()}"
    if meeting:
        payload += f"\n\n=== NOME DA REUNIÃO (do título da janela; pista, pode estar incompleto) ===\n{meeting}"
    payload += f"\n\n=== TRANSCRIÇÃO ===\n\n{transcript_md}"

    # piso = config; escala pela duração do áudio; teto p/ não pendurar o worker
    audio_min = float(meta.get("duration_seconds") or 0) / 60
    timeout = min(_SUMMARY_TIMEOUT_CEILING, max(int(cfg.timeout_seconds), int(audio_min * _SUMMARY_S_PER_AUDIO_MIN)))

    out = ai.complete(SYSTEM_PROMPT, payload, timeout=timeout, cwd=folder, hidden_window=False)
    if not out:
        return None, None, None
    return split_header(out)


def _export_stem(meta: dict, folder: Path) -> str:
    """Nome-base da nota exportada: data+hora do início ("2026-06-12_11-55").

    A pasta da gravação agora se chama só "HH-MM" (dentro de ano\\mês\\dia), então
    o nome vem do started_at — preservando o sufixo _N de gravações no mesmo minuto.
    Pastas legadas (nome longo) caem no fallback folder.name.
    """
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(meta.get("started_at", ""))
    except ValueError:
        return folder.name
    import re

    # sufixo _N só do prefixo de hora ("16-34_2[_Título]"), nunca de um título
    # que por acaso termine em _<dígitos>
    m = re.match(r"^(?:\d{4}-\d{2}-\d{2}_)?\d{2}-\d{2}(_\d+)?", folder.name)
    suffix = m.group(1) if m and m.group(1) else ""
    return dt.strftime("%Y-%m-%d_%H-%M") + suffix


def _fmt_mmss(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


_STREAM_LABELS = {"mic": "microfone (Eu)", "loopback": "áudio do sistema (Participantes)"}


def _integrity_warning(meta: dict) -> str:
    """Callout "Áudio incompleto" para o topo do notas.md, ou "" se está tudo lá.

    Dois casos: gravação órfã adotada pós-crash (interrupted — a call seguiu sem
    captura) e stream que parou/não captou no meio de uma call encerrada normal
    (audio_seconds da captura bem menor que a duração da call). Quem lê a nota —
    humano ou IA — precisa saber que a transcrição não cobre a call inteira.
    """
    dur = float(meta.get("duration_seconds", 0) or 0)
    if meta.get("interrupted"):
        return (
            "> ⚠️ **Gravação interrompida no meio da call** (o app foi encerrado durante a "
            f"reunião). A transcrição cobre apenas os primeiros {_fmt_mmss(dur)} captados; "
            "o que veio depois se perdeu.\n\n"
        )
    if dur <= 0:
        return ""
    problems = []
    for key, s in (meta.get("streams") or {}).items():
        audio = s.get("audio_seconds")
        if audio is None:
            continue  # gravação antiga, sem o campo — nada a afirmar
        label = _STREAM_LABELS.get(key, key)
        if float(audio) <= 1.0:
            problems.append(f"o {label} não captou nada")
        elif dur - float(audio) > max(15.0, dur * 0.02):
            problems.append(
                f"a captura do {label} parou aos {_fmt_mmss(float(audio))} (call de {_fmt_mmss(dur)})"
            )
    if not problems:
        return ""
    return (
        "> ⚠️ **Áudio incompleto** — "
        + "; ".join(problems)
        + ". A transcrição cobre só o que foi gravado.\n\n"
    )


def build_notes(folder: Path) -> Path | None:
    """Monta o notas.md (frontmatter + resumo + transcrição) e exporta a cópia final."""
    folder = Path(folder)
    meta_path = folder / "meta.json"
    transcript_path = folder / "transcript.json"
    if not transcript_path.exists():
        print(f"transcript.json não existe em {folder} — rode antes: scriba transcribe \"{folder}\"")
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    turns = [merge_mod.Turn(**d) for d in json.loads(transcript_path.read_text(encoding="utf-8"))]
    transcript_md = merge_mod.render_markdown(turns)

    # estágio para a UI: "Gerando resumo…"
    meta["status"] = "summarizing"
    util.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))

    print("gerando resumo estruturado...")
    summary, title, client = generate_summary(transcript_md, folder)
    has_summary = summary is not None
    if summary is None:
        summary = f'> Resumo indisponível — rode: scriba summarize "{folder}"'

    started = meta.get("started_at", "")
    duration_min = int(float(meta.get("duration_seconds", 0)) // 60)
    when = started.replace("T", " ")[:16] if started else folder.name
    if not title:
        title = meta.get("title") or f"Reunião {when[-5:] if when else ''}".strip()
    if not client:
        client = meta.get("client") or ""  # preserva cliente editado à mão

    # O enquadramento só faz sentido quando há resumo estruturado de verdade;
    # com o placeholder de falha ele referenciaria seções inexistentes.
    note = load_context_note()
    context_note = f"{note}\n\n" if (has_summary and note) else ""

    # linha de metadados visível, sob o título: data · duração · cliente
    meta_line = f"*{when}" + (f" · {duration_min} min" if duration_min else "")
    meta_line += f" · Cliente: {client}*" if client else "*"

    md = (
        "---\n"
        f"titulo: {title}\n"
        f"cliente: {client}\n"
        f"data: {started}\n"
        f"fim: {meta.get('ended_at', '')}\n"
        f"duracao_minutos: {duration_min}\n"
        "origem: scriba\n"
        f"whisper: {meta.get('whisper_model', '')} ({meta.get('whisper_device', '')})\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{meta_line}\n\n"
        f"{_integrity_warning(meta)}"
        f"{context_note}"
        f"{summary}\n\n"
        "---\n\n"
        "## Transcrição completa\n\n"
        f"{transcript_md}\n"
    )
    # escrita atômica: notas.md é a saída final para o usuário — write_text direto
    # trunca o arquivo se o processo morrer no meio; replace() preserva o anterior
    md_path = folder / "notas.md"
    util.atomic_write_text(md_path, md)

    export_dir = load().output.resolved_export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)
    export_path = export_dir / f"{_export_stem(meta, folder)}_reuniao.md"
    shutil.copyfile(md_path, export_path)

    meta["status"] = "done"
    meta["title"] = title
    meta["client"] = client
    meta["export_path"] = str(export_path)
    util.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
    # indexa p/ busca (#10): o índice é derivado/reconstruível — falha aqui NUNCA
    # pode quebrar a geração da nota (index_meeting já engole exceções, mas o import
    # fica protegido por garantia).
    try:
        from . import meetings_index

        meetings_index.index_meeting(folder)
    except Exception:
        pass
    print(f"notas prontas: {export_path}")
    return export_path


def set_note_client(md_path: Path, new_client: str) -> None:
    """Atualiza o cliente de uma nota: linha `cliente:` do frontmatter + linha de
    metadados sob o título (`*data · N min · Cliente: X*`). Vazio remove o cliente."""
    import re

    new_client = new_client.strip()
    if not md_path.exists():
        return
    lines = md_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_front = bool(lines and lines[0].strip() == "---")
    front_closed = not in_front
    cliente_done = False
    h1_seen = False
    meta_done = False
    for i, line in enumerate(lines):
        if in_front and not front_closed:
            if i > 0 and line.strip() == "---":
                if not cliente_done:
                    out.append(f"cliente: {new_client}")
                    cliente_done = True
                front_closed = True
            elif line.startswith("cliente:") and not cliente_done:
                out.append(f"cliente: {new_client}")
                cliente_done = True
                continue
        if front_closed and not meta_done:
            if line.startswith("# "):
                h1_seen = True
            elif h1_seen and line.strip():
                # primeira linha não-vazia após o H1: é a linha de metadados
                # (*data · duração[ · Cliente: X]*) — se não for, não mexe no corpo
                m = re.fullmatch(r"\*(.+?)(?: · Cliente: .+)?\*", line.strip())
                if m:
                    base = m.group(1)
                    out.append(f"*{base} · Cliente: {new_client}*" if new_client else f"*{base}*")
                    meta_done = True
                    continue
                meta_done = True
        out.append(line)
    util.atomic_write_text(md_path, "\n".join(out) + "\n")


def relabel_speakers(folder: Path, renames: dict[str, str]) -> bool:
    """Aplica rótulos de voz (#1) a uma reunião já processada:

    1. aprende cada voz (speakers.enroll com o embedding salvo em voices.json) —
       o app passa a reconhecê-la nas próximas reuniões;
    2. troca "Participante N" → Nome na transcrição (transcript.json) e na nota
       (notas.md local + cópia exportada), por substituição de texto — SEM re-rodar
       a IA: rápido e a nota fica coerente na hora.

    `renames`: {rótulo_atual: novo_nome}. Retorna True se algo mudou.
    """
    folder = Path(folder)
    renames = {k: v.strip() for k, v in renames.items() if v and v.strip() and v.strip() != k}
    if not renames:
        return False
    from . import speakers

    # 1) aprende as vozes a partir dos embeddings guardados na pasta
    voices_path = folder / "voices.json"
    try:
        voices = json.loads(voices_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        voices = {}
    for label, name in renames.items():
        emb = (voices.get(label) or {}).get("embedding")
        if emb:
            speakers.enroll(name, emb)

    # 2) transcript.json: troca o campo speaker (comparação exata)
    tpath = folder / "transcript.json"
    try:
        turns = json.loads(tpath.read_text(encoding="utf-8"))
        for t in turns:
            if t.get("speaker") in renames:
                t["speaker"] = renames[t["speaker"]]
        util.atomic_write_text(tpath, json.dumps(turns, ensure_ascii=False, indent=1))
    except (OSError, ValueError):
        pass

    # 3) markdown (nota local + exportada): "Participante N" → Nome, com \b para
    # não casar "Participante 1" dentro de "Participante 12"
    subs = [(re.compile(r"\b" + re.escape(label) + r"\b"), name) for label, name in renames.items()]
    targets = [folder / "notas.md"]
    try:
        exp = json.loads((folder / "meta.json").read_text(encoding="utf-8")).get("export_path")
        if exp:
            targets.append(Path(exp))
    except (OSError, ValueError):
        pass
    for md in targets:
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for rx, name in subs:
            text = rx.sub(name, text)
        util.atomic_write_text(md, text)

    # 4) voices.json: renomeia as chaves e marca como rotulada à mão
    if voices:
        for label, name in renames.items():
            if label in voices:
                v = voices.pop(label)
                v["auto"] = False
                v["labeled"] = True
                voices[name] = v
        try:
            util.atomic_write_text(voices_path, json.dumps(voices, ensure_ascii=False))
        except OSError:
            pass
    # re-indexa (#10): nomes de participantes mudaram → atualiza a busca
    try:
        from . import meetings_index

        meetings_index.index_meeting(folder)
    except Exception:
        pass
    return True


_PART_HEADER = re.compile(r"(?i)^\s*##\s+participantes\s*$")
# sub-blocos: a IA escreve tanto "**Presentes:**" quanto "### Presentes" (sub-cabeçalho)
_PRES_MARK = re.compile(r"(?i)^(?:#{3,6}\s+|\*\*)\s*presentes")
_MENC_MARK = re.compile(r"(?i)^(?:#{3,6}\s+|\*\*)\s*mencionad")
# fim da seção = próximo cabeçalho de nível 1-2; ### internos NÃO encerram (o bug: um
# "### Presentes" começa com "## " e fazia o parser parar antes de ler os participantes)
_SECTION_END = re.compile(r"^#{1,2}\s+\S")
_PART_BULLET = re.compile(r"^[-*]\s+\*\*(.+?)\*\*\s*[—:–-]?\s*(.*)$")
# nome embutido no rótulo: "Participante 1 (Guilherme Lima)" -> voz "Participante 1" + nome
_LABEL_NAME = re.compile(r"(?i)^(participante\s+\d+)\s*\(([^)]+)\)\s*$")
# palpite de nome: "Alex (…" ou "Guilherme Lima (…" no começo (1-4 palavras capitalizadas),
# ou "… provavelmente/possivelmente/identificado X"
_GUESS_LEAD = re.compile(r"^\s*([A-ZÀ-Ý][\wÀ-ÿ/]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ/]+){0,3})\s*\(")
_GUESS_MARK = re.compile(
    r"(?i:provavelmente|possivelmente|identificad[oa]|talvez)"   # marcador (case-insensitive)
    r"[\s:]+(?:(?:como|o|a|os|as)\s+)*"                          # separador + 'como'/artigo opcionais
    "[\"'“”‘’]?"                             # aspas opcional (retas ou curvas)
    r"([A-ZÀ-Ý][\wÀ-ÿ]+)"                                       # nome (capitalizado; 1o token, ate '/')
)


def parse_participants(md: str) -> tuple[dict[str, str], list[str]]:
    """Lê a seção `## Participantes` do resumo: (presentes, mencionados).

    `presentes` mapeia o rótulo → a descrição/palpite da IA (ex.: "Participante 2"
    → "Alex (identificado: …)"). `mencionados` é a lista de nomes citados que não
    falaram. Tolerante ao formato da IA: os sub-blocos vêm tanto como **Presentes:**
    quanto como ### Presentes, e o nome pode vir embutido no rótulo ("Participante 1
    (Guilherme Lima)"). Best-effort: sem a seção → ({}, [])."""
    lines = md.splitlines()
    start = next((i + 1 for i, ln in enumerate(lines) if _PART_HEADER.match(ln)), None)
    if start is None:
        return {}, []
    presentes: dict[str, str] = {}
    mencionados: list[str] = []
    mode = "pres"  # a seção começa em Presentes; só vira "menc" ao achar o marcador
    for ln in lines[start:]:
        s = ln.strip()
        if _SECTION_END.match(s):  # próximo H1/H2 (### internos não encerram) = fim
            break
        if _PRES_MARK.match(s):
            mode = "pres"
            continue
        if _MENC_MARK.match(s):
            mode = "menc"
            continue
        mb = _PART_BULLET.match(s)
        if not mb:
            continue
        label, desc = mb.group(1).strip(), mb.group(2).strip()
        nm = _LABEL_NAME.match(label)
        if nm:  # "Participante 1 (Guilherme Lima)" -> chave "Participante 1" + nome no começo da desc
            label = nm.group(1).strip()
            desc = f"{nm.group(2).strip()} (" + (desc or "presente") + ")"
        if mode == "pres":
            presentes[label] = desc
        else:
            mencionados.append(label)
    return presentes, mencionados


def guess_voice_name(label: str, desc: str) -> str:
    """Palpite de nome para uma voz, a partir da descrição da IA em Presentes.

    Voz já nomeada (rótulo não é "Participante N") → o próprio rótulo. Senão tenta
    "Nome (…" no início da descrição ou "provavelmente/possivelmente/identificado X".
    Retorna "" quando a IA não cravou um nome (o usuário digita)."""
    if not label.startswith("Participante "):
        return label
    m = _GUESS_LEAD.match(desc)
    if m:
        return m.group(1)
    m = _GUESS_MARK.search(desc)
    if m:
        return m.group(1)
    return ""


def set_note_title(md_path: Path, new_title: str) -> None:
    """Atualiza o título de uma nota: linha `titulo:` do frontmatter + primeiro H1."""
    new_title = new_title.strip()
    if not new_title or not md_path.exists():
        return
    lines = md_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_front = bool(lines and lines[0].strip() == "---")
    front_closed = not in_front
    titulo_done = False
    h1_done = False
    for i, line in enumerate(lines):
        if in_front and not front_closed:
            if i > 0 and line.strip() == "---":
                if not titulo_done:
                    out.append(f"titulo: {new_title}")
                    titulo_done = True
                front_closed = True
            elif line.startswith("titulo:") and not titulo_done:
                out.append(f"titulo: {new_title}")
                titulo_done = True
                continue
        if front_closed and not h1_done and line.startswith("# "):
            out.append(f"# {new_title}")
            h1_done = True
            continue
        out.append(line)
    util.atomic_write_text(md_path, "\n".join(out) + "\n")
