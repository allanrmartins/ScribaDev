"""Gera o "Prompt de Contexto" de uma reunião (issue #3).

Reembala o resumo da nota num prompt pronto para colar no Claude Code: em vez do
texto cru da tela, entrega uma MOLDURA instrutiva + o miolo do resumo, para que o
Claude entenda que aquilo é o CONTEXTO de uma call (mistura de spec documentada e
fala, não canônica) e o absorva no gap em tratamento — em vez de responder sobre
a reunião.

Módulo PURO (só stdlib, sem Tkinter e sem I/O): a janela de Notas (desktop) e o
backend do Scriba nuvem chamam a MESMA função `build_context_prompt`. Por isso a
extração de frontmatter/seções é feita aqui, e não via `mdview` (que puxa Tk).
"""

from __future__ import annotations

_TRANSCRIPT_TITLE = "transcrição completa"


def _split_frontmatter(md: str) -> tuple[dict[str, str], str]:
    """(frontmatter, corpo). Frontmatter = bloco `k: v` entre `---` no topo."""
    lines = md.splitlines()
    if not (lines and lines[0].strip() == "---"):
        return {}, md
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            front: dict[str, str] = {}
            for ln in lines[1:i]:
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    front[k.strip().lower()] = v.strip()
            return front, "\n".join(lines[i + 1:]).lstrip("\n")
    return {}, md


def _split_sections(body: str) -> list[tuple[str, str]]:
    """[(título H2, corpo)…] — parsing puro (espelha mdview.split_sections, sem Tk)."""
    secs: list[list] = []
    for line in body.splitlines():
        if line.startswith("## "):
            secs.append([line[3:].strip(), []])
        elif secs:
            secs[-1][1].append(line)
    return [(t, "\n".join(b).strip()) for t, b in secs]


def _summary_body(note_md: str) -> str:
    """Miolo do resumo: só as seções H2, exceto a transcrição. Descarta o que vem
    antes da 1ª seção (H1, linha de metadados e o callout antigo "Contexto para IA"),
    pois o enquadramento agora é dado pela moldura."""
    _front, body = _split_frontmatter(note_md)
    parts = []
    for title, text in _split_sections(body):
        if title.strip().lower() == _TRANSCRIPT_TITLE or not text.strip():
            continue
        parts.append(f"## {title}\n{text}")
    return "\n\n".join(parts).strip()


def _field(meta: dict | None, front: dict, *keys: str) -> str:
    """1º valor não-vazio para `keys`, procurando antes no `meta` e depois no frontmatter."""
    for src in (meta or {}, front):
        for k in keys:
            v = src.get(k)
            if v:
                return str(v).strip()
    return ""


# Moldura instrutiva: diz ao Claude Code o que é o material e como usá-lo. Mantida
# como constante para o desktop e o Scriba nuvem produzirem prompts idênticos.
_INSTRUCTIONS = (
    "O texto abaixo é o registro de uma reunião de trabalho (transcrita "
    "automaticamente e resumida). Trate-o como CONTEXTO de apoio para a atividade "
    "que você está tratando agora:\n\n"
    "- NÃO é um documento canônico — é uma mistura da especificação documentada "
    "com o que foi falado na call (transcrição automática, pode conter imprecisões "
    "e ruído).\n"
    "- ABSORVA daqui os requisitos, regras de negócio e decisões relevantes ao gap "
    "em tratamento e siga com o trabalho; não responda sobre a reunião nem a resuma "
    "de volta.\n"
    "- Onde houver lacunas ou ambiguidades (ver \"Pendências e Ações\"), sinalize "
    "em vez de presumir."
)


def build_context_prompt(note_md: str, meta: dict | None = None) -> str:
    """Monta o Prompt de Contexto a partir do markdown da nota de uma reunião.

    `note_md`: o conteúdo do notas.md (com frontmatter e resumo).
    `meta`: metadados opcionais (title/client/date) — o backend cloud pode passá-los
    em vez do frontmatter; na falta, são extraídos do próprio frontmatter.

    Puro: mesma saída no desktop e no Scriba nuvem.
    """
    front, _body = _split_frontmatter(note_md)
    title = _field(meta, front, "title", "titulo") or "(sem título)"
    client = _field(meta, front, "client", "cliente")
    date = _field(meta, front, "date", "data")

    head = f'# Contexto de reunião — "{title}"'
    tags = []
    if client:
        tags.append(f"Cliente: {client}")
    if date:
        tags.append(f"Data: {date[:16].replace('T', ' ')}")
    if tags:
        head += "\n" + " · ".join(tags)

    summary = _summary_body(note_md) or "(esta reunião ainda não tem resumo estruturado.)"
    return f"{head}\n\n{_INSTRUCTIONS}\n\n---\n\n{summary}\n"
