"""Geração do prompt.md personalizado por perfil (wizard de onboarding) — issue #2.

A geração por IA é a via principal (claude -p, como o resumo); templates locais
por perfil são o fallback offline. Todo prompt — gerado ou de template — passa
pelo validador do CONTRATO ESTRUTURAL com o leitor de notas (mdview): seções
## H2, tabela com linha |---|, checklists "- [ ]", "Nada identificado.",
timestamps [HH:MM:SS], só markdown sem cercas. TITULO:/CLIENTE: ficam fora
daqui (instrução do código, sempre prepended em notes.generate_summary).
"""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import config as config_mod
from . import util

VALID_MIN_CHARS = 800


@dataclass(frozen=True)
class Profile:
    base: str = "generic"  # abap | dev | functional | pm | generic
    role: str = ""         # profissão/papel (livre)
    area: str = ""         # área/indústria
    stack: str = ""        # stack/ferramentas
    jargon: str = ""       # termos frequentes (livre)
    must_have: str = ""    # o que não pode faltar na ata


BASE_LABELS = {
    "abap": "Dev SAP / ABAP",
    "dev": "Dev de software (outras stacks)",
    "functional": "Analista funcional / BA",
    "pm": "Gerente de projetos",
    "generic": "Geral / outra profissão",
}


# ------------------------------------------------------------ templates locais --

# Esqueleto comum dos templates de fallback. Os slots por perfil mudam quem fala,
# os tipos de atividade, o guia do Detalhamento e a tabela típica — a ESTRUTURA
# (H2, tabela, checklist, "Nada identificado.", timestamps) é a mesma do prompt
# ABAP original e é o que o leitor de notas espera.
_BASE_TEMPLATE = """\
A seguir está a transcrição com timestamps de uma reunião (Teams/Zoom/Meet){context_line}, \
em português do Brasil. {speakers}

A reunião pode ser sobre QUALQUER atividade do dia a dia: {activities}. \
NÃO assuma o tipo: identifique a atividade real pelo conteúdo da conversa.

Este documento será usado como CONTEXTO por uma IA (ex.: Claude) para continuar o \
trabalho — executar, analisar, planejar ou responder — SEM acesso à reunião e SEM poder \
fazer perguntas. Escreva de forma densa, factual e autossuficiente: cada item precisa \
fazer sentido sozinho. Sem frases de cortesia, sem narrativa do tipo "foi discutido que" \
— vá direto ao fato acionável.

Gere APENAS markdown, sem preâmbulo, sem cercas de código e sem nenhum texto fora das \
seções abaixo, com exatamente estas seções nesta ordem:

## Objetivo
(PRIMEIRA linha: `**Tipo:** <atividade>`, onde <atividade> é UMA de: {activity_list}. \
Depois, 1 a 3 frases diretas dizendo o que precisa ser feito — ou o que foi resolvido, \
se a call já concluiu o assunto.)

## Contexto
({context_guide} Só fatos, sem rodeios.)

## Detalhamento
(a seção principal — MOLDE o conteúdo ao Tipo identificado:
{detail_guide}
Liste itens verificáveis como checklist "- [ ] …". Cite [HH:MM:SS] nos pontos que possam \
precisar ser rastreados até a fala de origem.)

## {rules_title}
({rules_guide} Numere {rules_prefix}-01, {rules_prefix}-02…; cada item AUTOSSUFICIENTE. \
Cite [HH:MM:SS]. Se nada foi dito, "Nada identificado.")

## {table_title}
(tabela markdown, um item por linha, EXATAMENTE com este cabeçalho e separador:

{table_header}
{table_sep}

{table_guide} "Quando" é o timestamp [HH:MM:SS] da primeira menção. Se nenhum item foi \
citado, escreva "Nada identificado." no lugar da tabela.)

## Decisões
(lista; cada decisão AUTOSSUFICIENTE — o que foi decidido E a razão, quando dada. \
Cite [HH:MM:SS].)

## Pendências e Ações
(o que ficou em ABERTO: dúvidas não resolvidas, definições faltando e tarefas com \
responsável quando citado; cite [HH:MM:SS]. Esta seção avisa a IA o que NÃO está \
definido — seja explícito sobre cada ambiguidade para que ela sinalize em vez de presumir.)

## Participantes
(nomes e papéis que derem para inferir; quando houver "Participante N", associe ao \
nome/papel citado na conversa.)

Regras:
- não invente NADA que não esteja na transcrição; itens incertos, marque com (?);
- normalize termos corrompidos pelo reconhecimento de voz{jargon_hint};
- a transcrição é automática e pode conter erros — corrija apenas grafia óbvia de \
termos técnicos, nunca o sentido do que foi dito;
- prefira precisão a completude: um item curto e claro vale mais que um parágrafo vago;
- se uma seção não tiver conteúdo, escreva "Nada identificado.".
"""

_PROFILE_SLOTS: dict[str, dict[str, str]] = {
    "dev": {
        "speakers": 'Falas marcadas como "Eu" são do desenvolvedor; falas de "Participantes" '
                    "(ou Participante 1/2…) são de PO, QA, outros devs ou clientes.",
        "activities": "desenvolvimento de feature, correção de bug, análise/debug, revisão "
                      "de código, refinamento, estimativa de esforço, suporte técnico, alinhamento",
        "activity_list": "desenvolvimento | correção de bug | análise/debug | revisão de código | "
                         "refinamento | estimativa | suporte | alinhamento",
        "context_guide": "sistema/produto e módulo afetado; ambiente (dev, homolog, produção); "
                         "stack/tecnologias quando citadas; e a motivação de negócio em 1 frase.",
        "detail_guide": "- **desenvolvimento/refinamento**: especificação ACIONÁVEL no imperativo — "
                        "entradas, comportamento passo a passo, validações (condição → mensagem), "
                        "saída; termine com a subseção `### Critérios de aceite` (checklist \"- [ ] …\");\n"
                        "- **bug / análise/debug**: sintoma exato, passos para reproduzir, o que JÁ "
                        "foi verificado e o resultado, hipóteses, próximos pontos de investigação;\n"
                        "- **estimativa**: escopo e entregáveis, premissas, dependências, riscos;\n"
                        "- **suporte**: o que foi perguntado e a solução dada (reproduzível).",
        "rules_title": "Regras de negócio",
        "rules_guide": "TODAS as regras de negócio, algoritmos e definições funcionais ditas na "
                       "reunião — mesmo as que não fazem parte direta da tarefa. Fluxos em passos "
                       "numerados na ordem.",
        "rules_prefix": "RN",
        "table_title": "Itens técnicos citados",
        "table_header": "| Tipo | Item | Observação | Quando |",
        "table_guide": "Tipos: repositório, serviço, endpoint/API, classe/módulo, tabela/coleção, "
                       "job, fila, ferramenta, biblioteca.",
    },
    "functional": {
        "speakers": 'Falas marcadas como "Eu" são do analista funcional; falas de "Participantes" '
                    "(ou Participante 1/2…) são de usuários-chave, clientes, devs ou gestores.",
        "activities": "levantamento de requisitos, definição de processo, validação/homologação, "
                      "análise de incidente, parametrização, estimativa, treinamento, alinhamento",
        "activity_list": "levantamento de requisitos | definição de processo | validação/homologação | "
                         "análise de incidente | parametrização | estimativa | treinamento | alinhamento",
        "context_guide": "processo de negócio e área envolvida; sistema/módulo; ambiente; "
                         "e a motivação em 1 frase.",
        "detail_guide": "- **levantamento/definição**: requisitos numerados (RF-01…) no imperativo, "
                        "com critérios verificáveis como checklist \"- [ ] …\";\n"
                        "- **validação/homologação**: o que foi testado, resultado de cada cenário, "
                        "o que reprovou e por quê;\n"
                        "- **incidente**: sintoma, impacto no negócio, o que JÁ foi verificado, "
                        "hipóteses e próximos passos;\n"
                        "- **parametrização/treinamento**: passo a passo reproduzível do que foi "
                        "configurado ou ensinado.",
        "rules_title": "Regras de negócio",
        "rules_guide": "TODAS as regras de negócio e definições de processo ditas na reunião. "
                       "Fluxos em passos numerados na ordem.",
        "rules_prefix": "RN",
        "table_title": "Itens citados",
        "table_header": "| Tipo | Item | Observação | Quando |",
        "table_guide": "Tipos: processo, sistema, módulo, transação/tela, relatório, indicador, documento.",
    },
    "pm": {
        "speakers": 'Falas marcadas como "Eu" são do gerente de projetos; falas de "Participantes" '
                    "(ou Participante 1/2…) são do time, clientes, fornecedores ou patrocinadores.",
        "activities": "status report, planejamento, kickoff, retrospectiva, gestão de riscos, "
                      "negociação de escopo/prazo, comitê, alinhamento",
        "activity_list": "status report | planejamento | kickoff | retrospectiva | gestão de riscos | "
                         "negociação | comitê | alinhamento",
        "context_guide": "projeto/iniciativa e fase atual; envolvidos/áreas; marcos relevantes; "
                         "e o objetivo da reunião em 1 frase.",
        "detail_guide": "- **status report**: progresso por frente, o que avançou, o que está "
                        "bloqueado e por quê, desvios de prazo/custo;\n"
                        "- **planejamento/kickoff**: entregáveis como checklist \"- [ ] …\", marcos "
                        "com datas, dependências e responsáveis;\n"
                        "- **retrospectiva**: o que funcionou, o que não funcionou, melhorias acordadas;\n"
                        "- **negociação/comitê**: posições das partes, o que foi acordado, concessões.",
        "rules_title": "Riscos e impedimentos",
        "rules_guide": "TODOS os riscos, impedimentos e dependências citados — com probabilidade/"
                       "impacto e mitigação quando mencionados.",
        "rules_prefix": "RI",
        "table_title": "Ações acordadas",
        "table_header": "| Ação | Responsável | Prazo | Quando |",
        "table_guide": "Uma linha por ação acordada na reunião; responsável e prazo exatamente "
                       "como citados (sem inventar datas).",
    },
    "generic": {
        "speakers": 'Falas marcadas como "Eu" são do dono destas notas; falas de "Participantes" '
                    "(ou Participante 1/2…) são dos demais presentes.",
        "activities": "tomada de decisão, repasse de informações, resolução de problema, "
                      "planejamento, negociação, acompanhamento, alinhamento",
        "activity_list": "decisão | informativa | resolução de problema | planejamento | "
                         "negociação | acompanhamento | alinhamento",
        "context_guide": "assunto e área envolvida; quem demandou; e o objetivo da reunião em 1 frase.",
        "detail_guide": "- **decisão/negociação**: alternativas discutidas, posições e o racional;\n"
                        "- **resolução de problema**: o problema, impacto, o que já foi tentado, "
                        "próximos passos;\n"
                        "- **planejamento/acompanhamento**: entregas e próximos passos como "
                        "checklist \"- [ ] …\", com responsáveis e datas citadas.",
        "rules_title": "Definições e acordos",
        "rules_guide": "TODAS as definições, acordos e esclarecimentos relevantes ditos na reunião.",
        "rules_prefix": "DF",
        "table_title": "Ações acordadas",
        "table_header": "| Ação | Responsável | Prazo | Quando |",
        "table_guide": "Uma linha por ação acordada; responsável e prazo exatamente como citados.",
    },
}


def template_prompt(profile: Profile) -> tuple[str, str]:
    """(prompt, hotwords) do template local do perfil — o fallback offline."""
    if profile.base == "abap":
        from .notes import DEFAULT_SUMMARY_PROMPT

        return DEFAULT_SUMMARY_PROMPT, config_mod.Whisper().hotwords
    slots = dict(_PROFILE_SLOTS.get(profile.base) or _PROFILE_SLOTS["generic"])
    parts = [p for p in (profile.role, profile.area) if p.strip()]
    slots["context_line"] = f" de {' — '.join(parts)}" if parts else ""
    jargon_bits = ", ".join(p.strip() for p in (profile.stack, profile.jargon) if p.strip())
    slots["jargon_hint"] = (
        f", em especial o jargão da área ({jargon_bits})" if jargon_bits else ""
    )
    slots["table_sep"] = "|" + "---|" * (slots["table_header"].count("|") - 1)
    prompt = _BASE_TEMPLATE.format(**slots)
    if profile.must_have.strip():
        prompt += (
            f"\nPrioridade do dono das notas — garanta cobertura especial a: "
            f"{profile.must_have.strip()}.\n"
        )
    return prompt, _hotwords_from(profile)


def _hotwords_from(profile: Profile) -> str:
    """Hotwords do Whisper a partir do stack/jargão informados (até ~40 termos)."""
    raw = f"{profile.stack} {profile.jargon}"
    seen: list[str] = []
    for tok in re.split(r"[,;/\s]+", raw):
        tok = tok.strip()
        if tok and len(tok) > 1 and tok.lower() not in (s.lower() for s in seen):
            seen.append(tok)
        if len(seen) >= 40:
            break
    return " ".join(seen)


# ------------------------------------------------------------------ validador --

def validate_prompt(text: str) -> list[str]:
    """Problemas do prompt frente ao contrato estrutural do leitor ([] = válido)."""
    problems: list[str] = []
    if len(text) < VALID_MIN_CHARS:
        problems.append(f"muito curto (<{VALID_MIN_CHARS} caracteres)")
    if len(re.findall(r"^## ", text, re.M)) < 4:
        problems.append("menos de 4 seções '## ' definidas")
    if not re.search(r"\|-{3,}\|", text):
        problems.append("sem tabela markdown com linha separadora |---|")
    if "Quando" not in text or "[HH:MM:SS]" not in text:
        problems.append("sem instrução de timestamps [HH:MM:SS]")
    if "- [ ]" not in text:
        problems.append('sem instrução de checklist "- [ ]"')
    if "Nada identificado." not in text:
        problems.append('sem a instrução "Nada identificado." para seções vazias')
    if "markdown" not in text.lower():
        problems.append("sem a instrução de gerar apenas markdown")
    if "```" in text:
        problems.append("contém cercas de código (```)")
    return problems


# ------------------------------------------------------------- geração por IA --

_META_SYSTEM = (
    "Você é um engenheiro de prompts. Escreve em português do Brasil instruções densas e "
    "diretas para um gerador de atas de reunião. Responde EXCLUSIVAMENTE no formato pedido, "
    "sem comentários, sem cercas de código e sem oferecer ajuda extra."
)

_META_PROMPT = """\
Escreva as INSTRUÇÕES (um prompt) que o ScribaDev — um programa que grava reuniões, transcreve \
localmente e gera atas em markdown — usará para transformar transcrições em atas \
personalizadas para o perfil abaixo. O prompt deve ter a mesma densidade e qualidade do \
EXEMPLO ao final (que atende um desenvolvedor SAP ABAP), porém moldado a este perfil.

PERFIL DO USUÁRIO:
- Profissão/papel: {role}
- Área/indústria: {area}
- Stack/ferramentas: {stack}
- Jargão e termos frequentes: {jargon}
- O que não pode faltar na ata: {must_have}

REGRAS INEGOCIÁVEIS — o prompt que você escrever DEVE impor à ata:
1. gerar APENAS markdown, sem preâmbulo, sem cercas de código, sem texto fora das seções;
2. seções `## ` (H2) com títulos fixos e em ordem — defina de 6 a 8 seções adequadas ao \
perfil, SEMPRE incluindo "## Objetivo" (cuja primeira linha é `**Tipo:** <atividade>`, com \
a lista fechada de atividades plausíveis do perfil), "## Decisões", "## Pendências e Ações" \
e "## Participantes";
3. ao menos UMA seção com tabela markdown — mostre o cabeçalho EXATO e a linha separadora \
(|---|...) no prompt — adequada ao perfil, com a última coluna "Quando" = timestamp \
[HH:MM:SS] da primeira menção;
4. ao menos uma instrução de checklist no formato "- [ ]" para itens verificáveis;
5. a instrução de escrever "Nada identificado." quando uma seção não tiver conteúdo;
6. citar timestamps [HH:MM:SS] nos itens rastreáveis;
7. não inventar NADA que não esteja na transcrição (itens incertos marcados com (?));
8. normalizar termos do jargão do perfil corrompidos pelo reconhecimento de voz \
(dê exemplos reais do jargão informado);
9. lembrar que falas "Eu" são do dono das notas e "Participantes"/"Participante N" são os demais.

FORMATO DA SUA RESPOSTA:
- PRIMEIRA linha: `HOTWORDS: ` seguida de 20 a 40 termos do jargão/stack do perfil \
separados por espaço (vocabulário que guiará a transcrição de voz) — nada além disso na linha;
- da segunda linha em diante, APENAS o texto do prompt (sem cercas, sem títulos seus, \
sem explicações).

EXEMPLO DE PROMPT BEM ESCRITO (para outro perfil — dev SAP ABAP):
{example}
"""


def _call_claude(payload: str, timeout: int) -> str | None:
    """Provedor de IA do wizard. Encaminha para a camada configurável (scriba/ai.py),
    que roteia para claude CLI / Ollama / OpenAI-compatível conforme [summary].provider.

    cwd=APP_DIR + hidden_window=True reproduzem o comportamento do claude CLI na
    bandeja (pythonw sem console): CREATE_NO_WINDOW mantém o console invisível, sem
    o terminal que apareceria no meio do wizard. None se indisponível/falhou.
    """
    from . import ai

    return ai.complete(_META_SYSTEM, payload, timeout=timeout, cwd=util.APP_DIR, hidden_window=True)


def _split_hotwords(text: str) -> tuple[str, str]:
    """Separa a linha `HOTWORDS: ...` do início. (prompt, hotwords)."""
    first, _, rest = text.partition("\n")
    stripped = first.strip()
    if stripped.upper().startswith("HOTWORDS:"):
        return rest.strip(), stripped.split(":", 1)[1].strip()
    return text.strip(), ""


def ai_prompt(profile: Profile, timeout: int = 180) -> tuple[str, str] | None:
    """(prompt, hotwords) gerados por IA e validados — None se indisponível/inválido.

    Uma tentativa + um retry apontando os problemas; quem chama decide o fallback
    (template_prompt). Roda no claude CLI do usuário — local ao plano dele, sem
    custo nosso; no produto isso vai para o backend com cota por license key.
    """
    from .notes import DEFAULT_SUMMARY_PROMPT

    payload = _META_PROMPT.format(
        role=profile.role or BASE_LABELS.get(profile.base, "profissional"),
        area=profile.area or "(não informada)",
        stack=profile.stack or "(não informada)",
        jargon=profile.jargon or "(não informado)",
        must_have=profile.must_have or "(critério do gerador)",
        example=DEFAULT_SUMMARY_PROMPT,
    )
    for attempt in range(2):
        out = _call_claude(payload, timeout)
        if out is None:
            return None
        prompt, hotwords = _split_hotwords(out)
        problems = validate_prompt(prompt)
        if not problems:
            return prompt, (hotwords or _hotwords_from(profile))
        payload += (
            "\n\nSUA TENTATIVA ANTERIOR FOI REPROVADA PELO VALIDADOR. Corrija: "
            + "; ".join(problems) + ". Responda novamente no formato pedido."
        )
    return None


_JARGON_PROMPT = """\
Liste de 25 a 40 termos de jargão profissional que aparecem com FREQUÊNCIA em reuniões de \
trabalho do perfil abaixo — siglas, ferramentas, transações, artefatos, métricas e expressões \
típicas da área. Os termos vão guiar um reconhecedor de voz (Whisper) durante a transcrição \
das reuniões: prefira palavras e expressões curtas, como são ditas em voz alta, mantendo \
nomes técnicos no idioma original e o resto em português do Brasil.

PERFIL:
- Profissão/papel: {role}
- Área/indústria: {area}
- Stack/ferramentas: {stack}

Responda APENAS com os termos separados por vírgula, em uma única linha, sem numeração, \
sem aspas e sem nenhum comentário.
"""


def suggest_jargon(profile: Profile, timeout: int = 120) -> str | None:
    """Termos de jargão sugeridos por IA para o perfil ("a, b, c…"), ou None.

    O usuário raramente sabe listar o próprio vocabulário de cabeça — a IA conhece
    o jargão típico da profissão/área e devolve uma lista editável.
    """
    payload = _JARGON_PROMPT.format(
        role=profile.role or BASE_LABELS.get(profile.base, "profissional"),
        area=profile.area or "(não informada)",
        stack=profile.stack or "(não informada)",
    )
    out = _call_claude(payload, timeout)
    if not out:
        return None
    # tolera quebras de linha/numeração que escaparem; dedup preservando a ordem
    terms: list[str] = []
    for raw in re.split(r"[,\n;]+", out):
        term = re.sub(r"^\s*[\d\-•*.]+\s*", "", raw).strip().strip('"').strip()
        if term and len(term) < 40 and term.lower() not in (t.lower() for t in terms):
            terms.append(term)
    return ", ".join(terms) if len(terms) >= 5 else None


# ------------------------------------------------------------------- aplicar --

def apply_prompt(prompt_text: str, hotwords: str | None) -> Path | None:
    """Grava o prompt.md (com backup .bak do anterior) e as hotwords no config.

    hotwords None = não mexer nas atuais. Retorna o caminho do backup (ou None).
    """
    util.ensure_app_dirs()
    backup: Path | None = None
    if util.PROMPT_PATH.exists():
        old = util.PROMPT_PATH.read_text(encoding="utf-8")
        if old.strip() and old.strip() != prompt_text.strip():
            backup = util.PROMPT_PATH.with_suffix(".md.bak")
            backup.write_text(old, encoding="utf-8")
    util.atomic_write_text(util.PROMPT_PATH, prompt_text.strip() + "\n")
    if hotwords is not None:
        cfg = config_mod.load()
        config_mod.save(dataclasses.replace(
            cfg, whisper=dataclasses.replace(cfg.whisper, hotwords=hotwords)
        ))
    return backup


# ----------------------------------------------------------- estado do wizard --

def wizard_done() -> bool:
    try:
        return bool(json.loads(util.STATE_PATH.read_text(encoding="utf-8")).get("wizard_done"))
    except Exception:
        return False


def mark_wizard_done() -> None:
    try:
        data = {}
        if util.STATE_PATH.exists():
            data = json.loads(util.STATE_PATH.read_text(encoding="utf-8"))
        data["wizard_done"] = True
        util.atomic_write_text(util.STATE_PATH, json.dumps(data))
    except Exception:
        pass


def should_offer_on_boot() -> bool:
    """Primeiro uso de verdade: sem prompt.md ainda e wizard nunca concluído.

    Instalações existentes (prompt.md já criado) nunca são interrompidas —
    o wizard fica acessível pelo botão nas Configurações.
    """
    return not util.PROMPT_PATH.exists() and not wizard_done()
