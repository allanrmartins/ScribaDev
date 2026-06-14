"""Renderização leve de Markdown num tk.Text (subset usado pelas notas do ScribaDev).

Seções H2 são blocos colapsáveis (clique no título alterna ▾/▸), e tabelas
markdown viram tabelas de verdade (grid com cabeçalho, zebra e quebra de linha)
em vez de texto com pipes.
"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import font as tkfont

from .widgets import PALETTE

_VIEW_BG = "#2b2b34"
_INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")
_SEP_CELL = re.compile(r":?-+:?")

ARROW_OPEN, ARROW_CLOSED = "▾", "▸"


def setup_view(t: tk.Text) -> None:
    """Configura cores, fontes e tags de um tk.Text usado como leitor de notas."""
    t.configure(
        font=("Segoe UI", 10), bg=_VIEW_BG, fg=PALETTE["text"],
        padx=16, pady=12, wrap="word", borderwidth=0, highlightthickness=0,
        insertbackground=PALETTE["text"], selectbackground=PALETTE["accent"],
        spacing1=2, spacing3=4, cursor="arrow", state="disabled",
    )
    t.tag_configure("bold", font=("Segoe UI", 10, "bold"))
    t.tag_configure("code", font=("Consolas", 9), background=PALETTE["field"])
    t.tag_configure("h1", font=("Segoe UI", 16, "bold"), spacing1=10, spacing3=8)
    t.tag_configure("h2", font=("Segoe UI", 13, "bold"), foreground=PALETTE["accent_hover"], spacing1=12, spacing3=5)
    t.tag_configure("h3", font=("Segoe UI", 11, "bold"), spacing1=8, spacing3=4)
    t.tag_configure("bullet", lmargin1=16, lmargin2=30)
    t.tag_configure("quote", foreground=PALETTE["muted"], font=("Segoe UI", 10, "italic"), lmargin1=14, lmargin2=14)
    t.tag_configure("iline", foreground=PALETTE["muted"], font=("Segoe UI", 10, "italic"))
    t.tag_configure("meta", foreground=PALETTE["muted"], font=("Consolas", 8))
    t.tag_configure("hr", foreground=PALETTE["border"])
    t.tag_configure("table", font=("Consolas", 9))


# ------------------------------------------------------------- texto/markdown --


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


def parse_table_rows(lines: list[str]) -> list[list[str]] | None:
    """Linhas `| a | b |` -> células (sem a linha separadora). None se não for tabela."""
    rows: list[list[str]] = []
    for ln in lines:
        s = ln.strip()
        if not (s.startswith("|") and s.endswith("|") and len(s) > 2):
            return None
        cells = [c.strip() for c in s[1:-1].split("|")]
        if all(_SEP_CELL.fullmatch(c) for c in cells if c) and any(c for c in cells):
            continue  # |---|---|
        rows.append(cells)
    if not rows or max(len(r) for r in rows) < 2:
        return None
    return rows


def cell_text(cell: str) -> str:
    """Célula sem marcação inline (** e `)."""
    return cell.replace("**", "").replace("`", "").strip()


# ------------------------------------------------------------------ seções ----


def set_section_collapsed(t: tk.Text, key: int, flag: bool) -> None:
    meta = getattr(t, "_mdsections", {}).get(key)
    if not meta or meta.get("collapsed") == flag:
        return
    t.tag_configure(meta["body_tag"], elide=flag)
    prev = t.cget("state")
    t.configure(state="normal")
    hs = meta["head_start"]
    t.delete(hs, f"{hs}+1c")
    t.insert(hs, ARROW_CLOSED if flag else ARROW_OPEN, ("h2", meta["head_tag"]))
    t.configure(state=prev)
    meta["collapsed"] = flag


def toggle_section(t: tk.Text, key: int) -> None:
    meta = getattr(t, "_mdsections", {}).get(key)
    if meta:
        set_section_collapsed(t, key, not meta["collapsed"])


def expand_all(t: tk.Text) -> None:
    for key in getattr(t, "_mdsections", {}):
        set_section_collapsed(t, key, False)


# ------------------------------------------------------------------ tabelas ----


_COPY_LABEL = "⧉ copiar (Excel)"


def table_tsv(rows: list[list[str]]) -> str:
    """Tabela como TSV (cabeçalho incluído) — colável no Excel célula a célula."""
    return "\n".join("\t".join(cell_text(c) for c in r) for r in rows)


def _embed_table(t: tk.Text, rows: list[list[str]]) -> None:
    """Insere uma tabela como grid de labels (cabeçalho, zebra, quebra de linha),
    com um botão de copiar próprio — cada tabela da nota é copiável sozinha."""
    avail = t.winfo_width()
    if avail <= 1:
        avail = 560
    avail = max(360, avail - 60)

    base = tkfont.Font(family="Segoe UI", size=9)
    bold = tkfont.Font(family="Segoe UI", size=9, weight="bold")
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]

    want = [
        min(max(bold.measure(cell_text(r[ci])) + 22 for r in rows), int(avail * 0.45))
        for ci in range(ncols)
    ]
    total = sum(want)
    if total > avail:
        want = [max(64, int(w * avail / total)) for w in want]
    else:
        want[want.index(max(want))] += avail - total  # folga vai para a coluna larga

    wrap = tk.Frame(t, bg=_VIEW_BG)
    bar = tk.Frame(wrap, bg=_VIEW_BG)
    bar.pack(fill="x")
    link = tk.Label(bar, text=_COPY_LABEL, bg=_VIEW_BG, fg=PALETTE["muted"],
                    font=("Segoe UI", 8), cursor="hand2")
    link.pack(side="right", padx=(0, 2))

    tsv = table_tsv(rows)

    def _copy(_e=None):
        t.clipboard_clear()
        t.clipboard_append(tsv)
        link.configure(text="✓ copiado", fg=PALETTE["ok"])
        t.after(1500, lambda: link.configure(text=_COPY_LABEL, fg=PALETTE["muted"]))

    link.bind("<Button-1>", _copy)
    link.bind("<Enter>", lambda e: link.configure(fg=PALETTE["text"]) if link.cget("text") == _COPY_LABEL else None)
    link.bind("<Leave>", lambda e: link.configure(fg=PALETTE["muted"]) if link.cget("text") == _COPY_LABEL else None)

    box = tk.Frame(wrap, bg=PALETTE["border"])
    box.pack(anchor="w")
    widgets: list[tk.Widget] = [wrap, bar, link, box]
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
            head = ri == 0
            bg = "#3a3a4a" if head else ("#34343e" if ri % 2 else "#2f2f39")
            lbl = tk.Label(
                box, text=cell_text(cell), bg=bg,
                fg=PALETTE["accent_hover"] if head else PALETTE["text"],
                font=bold if head else base, anchor="nw", justify="left",
                wraplength=max(40, want[ci] - 16), padx=7, pady=4,
            )
            lbl.grid(row=ri, column=ci, sticky="nsew", padx=(0, 1), pady=(0, 1))
            widgets.append(lbl)
    for ci in range(ncols):
        box.grid_columnconfigure(ci, minsize=want[ci])

    def _wheel(e):
        t.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        return "break"

    for w in widgets:
        if w is not link:
            w.bind("<MouseWheel>", _wheel)
    link.bind("<MouseWheel>", _wheel, add="+")

    t.window_create("end", window=wrap)
    t.insert("end", "\n")


# ------------------------------------------------------------------- render ----


def _inline(t: tk.Text, text: str, base: tuple[str, ...] | str | None = None) -> None:
    if base is None:
        base_tags: tuple[str, ...] = ()
    elif isinstance(base, str):
        base_tags = (base,)
    else:
        base_tags = base
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            t.insert("end", part[2:-2], base_tags + ("bold",))
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            t.insert("end", part[1:-1], base_tags + ("code",))
        else:
            t.insert("end", part, base_tags)


def render(t: tk.Text, md: str, collapsed: set[str] | None = None) -> None:
    """Substitui o conteúdo pela nota renderizada.

    collapsed: títulos de seções H2 que começam fechadas (ex.: {"Transcrição completa"}).
    """
    collapsed_titles = {c.strip().lower() for c in (collapsed or set())}
    t.configure(state="normal")
    # limpa as seções da renderização anterior (configuração de tag persiste por nome)
    for meta in getattr(t, "_mdsections", {}).values():
        t.tag_configure(meta["body_tag"], elide=False)
        t.tag_delete(meta["head_tag"])
        t.tag_delete(meta["body_tag"])
    t._mdsections = {}
    t.delete("1.0", "end")

    lines = md.splitlines()
    i = 0
    # frontmatter YAML vira um bloco discreto no topo
    if lines and lines[0].strip() == "---":
        j = 1
        while j < len(lines) and lines[j].strip() != "---":
            j += 1
        if j < len(lines):
            for k in range(1, j):
                t.insert("end", lines[k] + "\n", "meta")
            t.insert("end", "\n")
            i = j + 1

    sec_i = -1
    body_start: str | None = None

    def _close_body() -> None:
        nonlocal body_start
        if sec_i >= 0 and body_start is not None:
            t.tag_add(t._mdsections[sec_i]["body_tag"], body_start, t.index("end-1c"))
        body_start = None

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        # bloco de tabela markdown -> tabela de verdade
        if s.startswith("|") and s.endswith("|") and len(s) > 2:
            j = i
            tbl: list[str] = []
            while j < len(lines):
                sj = lines[j].strip()
                if sj.startswith("|") and sj.endswith("|") and len(sj) > 2:
                    tbl.append(sj)
                    j += 1
                else:
                    break
            rows = parse_table_rows(tbl)
            if rows:
                _embed_table(t, rows)
                i = j
                continue
        i += 1
        if not s:
            t.insert("end", "\n")
        elif s.startswith("## "):
            _close_body()
            sec_i += 1
            head_tag, body_tag = f"mdsec_h{sec_i}", f"mdsec_b{sec_i}"
            head_start = t.index("end-1c")
            t.insert("end", f"{ARROW_OPEN} ", ("h2", head_tag))
            _inline(t, s[3:], ("h2", head_tag))
            t.insert("end", "\n", ("h2", head_tag))
            t._mdsections[sec_i] = {
                "head_tag": head_tag, "body_tag": body_tag,
                "head_start": head_start, "title": s[3:].strip(), "collapsed": False,
            }
            t.tag_bind(head_tag, "<Button-1>", lambda e, k=sec_i: toggle_section(t, k))
            t.tag_bind(head_tag, "<Enter>", lambda e: t.configure(cursor="hand2"))
            t.tag_bind(head_tag, "<Leave>", lambda e: t.configure(cursor="arrow"))
            body_start = t.index("end-1c")
        elif s.startswith("### "):
            _inline(t, s[4:], "h3")
            t.insert("end", "\n")
        elif s.startswith("# "):
            _inline(t, s[2:], "h1")
            t.insert("end", "\n")
        elif s in ("---", "***", "___"):
            t.insert("end", "─" * 72 + "\n", "hr")
        elif s[:5].lower() in ("- [ ]", "- [x]"):
            t.insert("end", ("☑  " if s[3].lower() == "x" else "☐  "), ("bullet",))
            _inline(t, s[5:].lstrip(), "bullet")
            t.insert("end", "\n")
        elif s.startswith(("- ", "* ")):
            t.insert("end", "•  ", ("bullet",))
            _inline(t, s[2:], "bullet")
            t.insert("end", "\n")
        elif re.match(r"^\d+\.\s", s):
            num, rest = s.split(" ", 1)
            t.insert("end", f"{num}  ", ("bullet",))
            _inline(t, rest, "bullet")
            t.insert("end", "\n")
        elif s.startswith("> "):
            _inline(t, s[2:], "quote")
            t.insert("end", "\n")
        elif (s.startswith("*") and s.endswith("*") and len(s) >= 3
              and not s.startswith(("**", "* ")) and not s.endswith("**")):
            # linha inteira em itálico (ex.: a linha de metadados sob o título:
            # *data · duração · Cliente: X*) — sem os asteriscos literais
            t.insert("end", s[1:-1] + "\n", "iline")
        elif s.startswith("|"):
            t.insert("end", line + "\n", "table")
        else:
            _inline(t, line)
            t.insert("end", "\n")
    _close_body()

    for key, meta in t._mdsections.items():
        if meta["title"].lower() in collapsed_titles:
            set_section_collapsed(t, key, True)
    t.configure(state="disabled")
