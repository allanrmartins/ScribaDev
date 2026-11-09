"""Prints da tela durante a call como momentos-chave (#229).

O usuário marca um momento importante tirando um print (botão da pílula ou tecla
do sistema). O arquivo vai para `prints/` na pasta da gravação e vira uma entrada
em `meta["screenshots"]`:

    {"at": "2026-11-09T14:32:05", "offset_seconds": 1925.3,
     "file": "prints/print-14h32m05s.png", "source": "pilula" | "tecla"}

`offset_seconds` está na MESMA linha do tempo da transcrição (segundos desde o
início da gravação), então o print entra na transcrição como uma marca no ponto
em que foi feito. A transcrição em si (`transcript.json`) continua só com falas:
as marcas são intercaladas na hora de renderizar.

No resumo, cada print é um momento-chave escolhido pelo usuário. A IA recebe a
instrução de descrevê-lo numa seção própria, e o código garante o resto: todo
print aparece na seção (mesmo que a IA esqueça) com a imagem logo abaixo.

Tudo aqui é puro (sem Qt), para testar sem tela.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

PRINTS_DIR = "prints"
SECTION_TITLE = "Momentos-chave"
MARKER_LABEL = "📸 Print (momento-chave)"
SOURCES = ("pilula", "tecla")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".heic", ".tiff", ".tif", ".gif", ".bmp")


# ------------------------------------------------------------------ arquivos --

def filename_for(when: datetime, suffix: str = ".png") -> str:
    """Nome do arquivo pelo horário: print-14h32m05s.png (legível na pasta e na nota)."""
    return f"print-{when:%Hh%Mm%Ss}{suffix.lower()}"


def unique_path(folder: Path, when: datetime, suffix: str = ".png") -> Path:
    """Caminho livre em <pasta>/prints/ para um print feito em `when`.
    Dois prints no mesmo segundo ganham _2, _3..."""
    base = Path(folder) / PRINTS_DIR
    name = filename_for(when, suffix)
    path = base / name
    n = 1
    while path.exists():
        n += 1
        path = base / f"{Path(name).stem}_{n}{Path(name).suffix}"
    return path


def entry(path: Path, folder: Path, when: datetime, offset: float, source: str) -> dict:
    """Entrada de `meta["screenshots"]` (caminho relativo à pasta, com barra normal)."""
    rel = Path(path).relative_to(Path(folder)).as_posix()
    return {
        "at": when.isoformat(timespec="seconds"),
        "offset_seconds": round(max(0.0, float(offset)), 1),
        "file": rel,
        "source": source if source in SOURCES else "pilula",
    }


def shots_of(meta: dict) -> list[dict]:
    """Prints válidos do meta, em ordem cronológica (entrada torta é ignorada)."""
    out = []
    for s in meta.get("screenshots") or []:
        try:
            off = float(s.get("offset_seconds"))
            f = str(s.get("file") or "").strip()
        except (AttributeError, TypeError, ValueError):
            continue
        if f:
            out.append({**s, "offset_seconds": off, "file": f})
    return sorted(out, key=lambda s: s["offset_seconds"])


# ----------------------------------------------------------- transcrição --

def fmt_ts(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def marker(shot: dict) -> str:
    """Parágrafo da marca na transcrição: mesmo carimbo das falas, para a IA (e quem
    lê) situar o print na conversa. O nome do arquivo é a âncora do resumo."""
    return f"**[{fmt_ts(shot['offset_seconds'])}] {MARKER_LABEL}:** {Path(shot['file']).name}"


def interleave(transcript_md: str, turn_starts: list[float], shots: list[dict]) -> str:
    """Intercala as marcas dos prints entre os parágrafos de fala da transcrição.

    `transcript_md` é o `merge.render_markdown` (um parágrafo por turno, separados
    por linha em branco) e `turn_starts` o início de cada turno, na mesma ordem.
    Cada marca entra antes do primeiro turno que começa DEPOIS do print.
    """
    if not shots:
        return transcript_md
    paras = transcript_md.split("\n\n") if transcript_md else []
    if len(paras) != len(turn_starts):  # formato inesperado: marcas no fim, nunca perdidas
        return "\n\n".join(paras + [marker(s) for s in shots])
    out: list[str] = []
    pending = list(shots)
    for start, para in zip(turn_starts, paras):
        while pending and pending[0]["offset_seconds"] < start:
            out.append(marker(pending.pop(0)))
        out.append(para)
    out.extend(marker(s) for s in pending)
    return "\n\n".join(out)


# ------------------------------------------------------------------ resumo --

def prompt_block(shots: list[dict]) -> str:
    """Instrução extra do resumo, só quando a reunião tem prints. Vai depois do
    prompt do usuário (prompt.md é editável e não sabe de prints), então autoriza
    explicitamente a seção a mais."""
    if not shots:
        return ""
    lista = "\n".join(f"- [{fmt_ts(s['offset_seconds'])}] {Path(s['file']).name}" for s in shots)
    return (
        "\n\n=== PRINTS DA TELA (MOMENTOS-CHAVE MARCADOS PELO USUÁRIO) ===\n"
        "Durante a reunião o usuário tirou prints da tela para marcar momentos importantes. "
        f"Na transcrição eles aparecem como \"{MARKER_LABEL}\" no ponto exato em que foram "
        "feitos. Cada print é um PONTO IMPORTANTE escolhido pelo usuário:\n"
        "1. Além das seções pedidas acima, inclua a seção "
        f"`## {SECTION_TITLE}` logo depois da PRIMEIRA seção do documento, com UM item por "
        "print, na ordem, EXATAMENTE neste formato:\n"
        "   `- [HH:MM:SS] nome-do-arquivo.png - <o que estava sendo discutido ou mostrado "
        "naquele momento e por que importa>`\n"
        "   Use a conversa em volta do horário do print (antes e logo depois) para dizer o "
        "assunto. Não invente o conteúdo da imagem: você não a vê.\n"
        "2. Dê destaque a esses assuntos nas demais seções (decisões, pendências, detalhes), "
        "citando o horário do print quando o ponto vier dele.\n"
        f"Prints desta reunião:\n{lista}"
    )


MAP_RULE = (
    f" Linhas \"{MARKER_LABEL}\" marcam prints da tela que o usuário tirou como momentos-chave: "
    "repita CADA uma, com o carimbo e o nome do arquivo, e diga o que estava sendo discutido "
    "naquele momento."
)


def _section_bounds(lines: list[str]) -> tuple[int, int] | None:
    """(linha do título, linha onde a seção termina) da seção Momentos-chave."""
    title = re.compile(rf"(?i)^\s*##\s+{re.escape(SECTION_TITLE)}\s*$")
    for i, ln in enumerate(lines):
        if title.match(ln):
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if re.match(r"^#{1,2}\s+\S", lines[j]):
                    end = j
                    break
            return i, end
    return None


def _insert_point(lines: list[str]) -> int:
    """Onde criar a seção quando a IA não criou: logo depois da primeira seção ##."""
    first = next((i for i, ln in enumerate(lines) if re.match(r"^##\s+\S", ln)), None)
    if first is None:
        return len(lines)
    for j in range(first + 1, len(lines)):
        if re.match(r"^#{1,2}\s+\S", lines[j]):
            return j
    return len(lines)


def attach(summary: str, shots: list[dict], link_prefix: str = PRINTS_DIR) -> str:
    """Garante a seção Momentos-chave no resumo, com TODO print listado e a imagem
    logo abaixo do item. Determinístico: o que a IA esqueceu o código completa.

    `link_prefix` é a pasta dos arquivos relativa ao .md (na pasta da gravação é
    `prints`; na cópia exportada, a pasta irmã da nota)."""
    if not shots:
        return summary
    lines = summary.rstrip("\n").split("\n")
    bounds = _section_bounds(lines)
    if bounds is None:
        at = _insert_point(lines)
        block = ["", f"## {SECTION_TITLE}", ""]
        if at > 0 and lines[at - 1].strip() == "":
            block = block[1:]
        lines[at:at] = block + ([""] if at < len(lines) else [])
        bounds = _section_bounds(lines)
    start, end = bounds
    body = lines[start + 1:end]
    # o que a IA já escreveu, sem imagens nossas de um processamento anterior
    body = [ln for ln in body if not ln.lstrip().startswith(("![Print", "[![Print"))]
    while body and body[-1].strip() == "":
        body.pop()
    while body and body[0].strip() == "":
        body.pop(0)
    # blocos: um item "- ..." leva junto as linhas seguintes que não são item (a IA
    # escreve uma frase por linha; a descrição de um print pode ocupar várias)
    blocks: list[list[str]] = []
    for ln in body:
        if not ln.strip():
            continue
        if re.match(r"^\s*[-*]\s", ln) or not blocks:
            blocks.append([ln])
        else:
            blocks[-1].append(ln)
    out: list[str] = []
    used: set[int] = set()
    for s in shots:
        name = Path(s["file"]).name
        src = f"{link_prefix}/{name}"
        img = f"  [![Print {fmt_ts(s['offset_seconds'])}]({src})]({src})"  # clique abre a imagem
        idx = next((i for i, b in enumerate(blocks) if i not in used and name in "\n".join(b)), None)
        if idx is None:
            out.append(f"- [{fmt_ts(s['offset_seconds'])}] {name} - momento marcado pelo usuário "
                       "(a IA não descreveu este print)")
        else:
            used.add(idx)
            out.extend(ln if j == 0 else "  " + ln.lstrip() for j, ln in enumerate(blocks[idx]))
        out.append(img)
    # blocos da IA que não casaram com nenhum print (texto solto) ficam antes
    extra = [ln for i, b in enumerate(blocks) if i not in used for ln in b]
    new_body = extra + ([""] if extra else []) + out
    lines[start + 1:end] = [""] + new_body + ([""] if end < len(lines) else [])
    return "\n".join(lines) + "\n"


def relink(md: str, link_prefix: str) -> str:
    """Troca o prefixo dos links de imagem dos prints (`](prints/`) por outro."""
    return md.replace(f"]({PRINTS_DIR}/", f"]({link_prefix}/")


# ------------------------------------------------------------ cópia exportada --

def export_dir_name(export_md: Path) -> str:
    """Pasta dos prints ao lado da nota exportada: 2026-11-09_14-30_reuniao.md ->
    2026-11-09_14-30_prints (nome único por reunião, como a nota)."""
    stem = Path(export_md).stem
    if stem.endswith("_reuniao"):
        stem = stem[: -len("_reuniao")]
    return f"{stem}_prints"


# ------------------------------------------------------------- corte da call --

def split_shots(shots: list[dict], offset: float) -> tuple[list[dict], list[dict]]:
    """Divide os prints no ponto de corte; os da parte 2 são rebaseados para o zero."""
    p1, p2 = [], []
    for s in shots:
        off = float(s.get("offset_seconds", 0.0))
        if off < offset:
            p1.append(dict(s))
        else:
            p2.append({**s, "offset_seconds": round(off - offset, 1)})
    return p1, p2


# ------------------------------------------------------- prints do sistema --

def new_system_shots(entries: list[tuple[str, float, int]], since: float,
                     seen: dict[str, int]) -> list[str]:
    """Quais arquivos da pasta de capturas do sistema são prints NOVOS e prontos.

    `entries` = [(nome, mtime, tamanho)], `since` = início da gravação (epoch) e
    `seen` = {nome: tamanho na ronda anterior}, atualizado aqui. Um arquivo só é
    devolvido quando o tamanho repete entre duas rondas (o sistema terminou de
    gravar) e nunca duas vezes (fica marcado com -1). Ignora oculto (o macOS
    escreve primeiro um ".Captura..." temporário), não-imagem e arquivo antigo.
    """
    ready = []
    for name, mtime, size in entries:
        if name.startswith(".") or Path(name).suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if mtime < since or size <= 0:
            continue
        prev = seen.get(name)
        if prev == -1:
            continue
        if prev == size:
            ready.append(name)
            seen[name] = -1
        else:
            seen[name] = size
    return ready
