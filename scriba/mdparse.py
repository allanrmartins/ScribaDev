"""Parsing puro de markdown (sem UI). Extraído de `mdview.py` no corte para Qt (#53):
o render tkinter da mdview morreu (o Qt usa QTextBrowser.setMarkdown), mas este
helper de estrutura segue sendo usado (nota Qt separa resumo de transcrição por aqui).
"""

from __future__ import annotations


def split_sections(md: str) -> tuple[str, list[tuple[str, str]]]:
    """Divide o markdown em (preâmbulo, [(título H2, corpo)...])."""
    pre: list[str] = []
    secs: list[tuple[str, list[str]]] = []
    for line in md.splitlines():
        if line.startswith("## "):
            secs.append((line[3:].strip(), []))
        elif secs:
            secs[-1][1].append(line)
        else:
            pre.append(line)
    return "\n".join(pre), [(t, "\n".join(b)) for t, b in secs]
