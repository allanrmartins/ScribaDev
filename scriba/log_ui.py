"""Janela de Log do ScribaDev (#18): visualizador do scriba.log — não um dump infinito.

- erros/críticos em vermelho (com o traceback junto), avisos em amarelo;
- busca com navegação (Enter / Shift+Enter + contador N/M);
- filtros: nível (Tudo / Avisos+ / Erros), dia (DD/MM/AAAA) e hora (a partir de);
- Copiar / Abrir pasta / **Exportar diagnóstico** (.zip pronto pra enviar ao dev).

A leitura/parse/filtro (puro) fica em diagnostics.py; aqui é só a UI.
"""

from __future__ import annotations

import logging
import tkinter as tk
from datetime import datetime

from . import diagnostics, util
from .widgets import (
    PALETTE,
    CalendarPopup,
    LinkLabel,
    ModernButton,
    add_placeholder,
    enable_dark_titlebar,
    make_entry,
    mask_date_br,
    mask_time_br,
)

log = logging.getLogger("scriba.log_ui")
_BG = PALETTE["bg"]
_MAX_LINES = 6000
_LEVELS_CYCLE = [("Tudo", 0), ("Avisos+", 30), ("Erros", 40)]


class LogWindow:
    def __init__(self, root: tk.Tk, app) -> None:
        self.app = app
        self._entries: list = []
        self._hits: list = []
        self._hit_idx = -1
        self._level_idx = 0
        self._render_job = None

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.title("ScribaDev — Log")
        self.win.configure(bg=_BG)
        self.win.geometry("960x600")
        self.win.minsize(620, 360)
        try:
            self.win.iconbitmap(str(util.ICON_ICO))
        except Exception:
            pass
        self.win.protocol("WM_DELETE_WINDOW", self.win.withdraw)  # fechar = esconder

        # ---- barra de cima: título + ações --------------------------------
        bar = tk.Frame(self.win, bg=_BG, padx=12, pady=8)
        bar.pack(fill="x")
        tk.Label(bar, text="Log do app", bg=_BG, fg=PALETTE["text"],
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        ModernButton(bar, "Exportar diagnóstico", self._export, kind="primary", width=180).pack(side="right")
        ModernButton(bar, "Abrir pasta", self._open_folder, width=100).pack(side="right", padx=(0, 8))
        ModernButton(bar, "Copiar", self._copy, width=80).pack(side="right", padx=(0, 8))

        # ---- linha 1: busca ------------------------------------------------
        row1 = tk.Frame(self.win, bg=_BG, padx=12, pady=2)
        row1.pack(fill="x")
        self.find_var = tk.StringVar()
        fe = make_entry(row1, self.find_var, width=26)
        fe.pack(side="left", ipady=2)
        add_placeholder(fe, self.find_var, "buscar no log…")
        fe.bind("<Return>", lambda e: self._next_hit())
        fe.bind("<Shift-Return>", lambda e: self._prev_hit())
        fe.bind("<Escape>", lambda e: (self.find_var.set(""), self._render()))
        LinkLabel(row1, "▲", self._prev_hit).pack(side="left", padx=(6, 0))
        LinkLabel(row1, "▼", self._next_hit).pack(side="left", padx=(2, 0))
        self.count_var = tk.StringVar(value="")
        tk.Label(row1, textvariable=self.count_var, bg=_BG, fg=PALETTE["muted"],
                 font=("Segoe UI", 8), width=7, anchor="w").pack(side="left", padx=(6, 0))
        self._auto = tk.BooleanVar(value=True)
        tk.Checkbutton(row1, text="auto-atualizar", variable=self._auto, bg=_BG, fg=PALETTE["muted"],
                       font=("Segoe UI", 8), activebackground=_BG, activeforeground=PALETTE["text"],
                       selectcolor=PALETTE["field"], bd=0, highlightthickness=0,
                       takefocus=False).pack(side="right")

        # ---- linha 2: filtros (nível + dia + hora, com datepicker) --------
        row2 = tk.Frame(self.win, bg=_BG, padx=12, pady=4)
        row2.pack(fill="x")
        self.level_btn = ModernButton(row2, "Nível: Tudo", self._cycle_level, width=120)
        self.level_btn.pack(side="left")
        tk.Label(row2, text="Dia:", bg=_BG, fg=PALETTE["muted"],
                 font=("Segoe UI", 8)).pack(side="left", padx=(14, 4))
        self.date_var = tk.StringVar()
        de = make_entry(row2, self.date_var, width=17)
        de.pack(side="left", ipady=2)
        mask_date_br(de, self.date_var)
        add_placeholder(de, self.date_var, "DD/MM/AAAA")
        LinkLabel(row2, "📅", lambda: self._open_calendar(de, self.date_var)).pack(side="left", padx=(4, 0))
        tk.Label(row2, text="Hora ≥", bg=_BG, fg=PALETTE["muted"],
                 font=("Segoe UI", 8)).pack(side="left", padx=(14, 4))
        self.time_var = tk.StringVar()
        te = make_entry(row2, self.time_var, width=7)
        te.pack(side="left", ipady=2)
        mask_time_br(te, self.time_var)
        add_placeholder(te, self.time_var, "HH:MM")

        self.status = tk.StringVar(value="")
        tk.Label(self.win, textvariable=self.status, bg=_BG, fg=PALETTE["muted"],
                 font=("Segoe UI", 8), anchor="w").pack(fill="x", padx=12)

        # ---- área do log --------------------------------------------------
        frame = tk.Frame(self.win, bg=_BG, padx=12, pady=8)
        frame.pack(fill="both", expand=True)
        self.text = tk.Text(frame, bg=PALETTE["log_bg"], fg=PALETTE["text"], insertbackground=PALETTE["text"],
                            font=("Consolas", 9), wrap="none", bd=0, highlightthickness=0, state="disabled")
        sb = tk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        sbx = tk.Scrollbar(frame, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=sb.set, xscrollcommand=sbx.set)
        sb.pack(side="right", fill="y")
        sbx.pack(side="bottom", fill="x")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("err", foreground=PALETTE["log_err"])   # ERROR/CRITICAL
        self.text.tag_configure("warn", foreground=PALETTE["warn"])     # WARNING
        self.text.tag_configure("debug", foreground=PALETTE["muted"])
        self.text.tag_configure("hit", background=PALETTE["highlight"], foreground=PALETTE["log_bg"])
        self.text.tag_configure("hit_current", background=PALETTE["highlight_current"], foreground=PALETTE["log_bg"])

        # filtros disparam re-render (debounce nos campos de texto)
        self.find_var.trace_add("write", lambda *a: self._debounced())
        self.date_var.trace_add("write", lambda *a: self._debounced())
        self.time_var.trace_add("write", lambda *a: self._debounced())

        self._reload()
        self.win.after(2000, self._tick)

    # ---- dados -----------------------------------------------------------
    def _reload(self) -> None:
        text = diagnostics.read_tail(diagnostics.LOG_FILE, _MAX_LINES)
        self._entries = diagnostics.parse_entries(text)
        self._render()

    def _render(self) -> None:
        self._render_job = None
        date_br = self.date_var.get().strip()
        date_br = date_br if len(date_br) == 10 else ""
        time_from = self.time_var.get().strip()
        time_from = time_from if len(time_from) == 5 else ""
        level_min = _LEVELS_CYCLE[self._level_idx][1]
        shown = diagnostics.filter_entries(self._entries, level_min, date_br, time_from)

        at_bottom = True
        try:
            at_bottom = self.text.yview()[1] >= 0.999
        except tk.TclError:
            pass

        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        n_err = n_warn = 0
        for e in shown:
            num = diagnostics.level_num(e.get("level", ""))
            tag = "err" if num >= 40 else "warn" if num == 30 else "debug" if num <= 10 else None
            if num >= 40:
                n_err += 1
            elif num == 30:
                n_warn += 1
            self.text.insert("end", e["text"] + "\n", (tag,) if tag else ())
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")
        self.status.set(f"{len(shown)} de {len(self._entries)} entradas · {n_err} erro(s) · {n_warn} aviso(s)")
        self._apply_search()

    def _debounced(self) -> None:
        if self._render_job is not None:
            try:
                self.win.after_cancel(self._render_job)
            except Exception:
                pass
        self._render_job = self.win.after(250, self._render)

    # ---- busca -----------------------------------------------------------
    def _apply_search(self) -> None:
        self.text.tag_remove("hit", "1.0", "end")
        self.text.tag_remove("hit_current", "1.0", "end")
        self._hits, self._hit_idx = [], -1
        term = self.find_var.get().strip()
        if not term:
            self.count_var.set("")
            return
        idx = "1.0"
        while True:
            pos = self.text.search(term, idx, nocase=1, stopindex="end")
            if not pos:
                break
            end = f"{pos}+{len(term)}c"
            self.text.tag_add("hit", pos, end)
            self._hits.append(pos)
            idx = end
        if self._hits:
            self._hit_idx = 0
            self._mark_current()
        else:
            self.count_var.set("0/0")

    def _mark_current(self) -> None:
        self.text.tag_remove("hit_current", "1.0", "end")
        if not self._hits:
            return
        pos = self._hits[self._hit_idx]
        self.text.tag_add("hit_current", pos, f"{pos}+{len(self.find_var.get().strip())}c")
        self.text.see(pos)
        self.count_var.set(f"{self._hit_idx + 1}/{len(self._hits)}")

    def _next_hit(self) -> None:
        if self._hits:
            self._hit_idx = (self._hit_idx + 1) % len(self._hits)
            self._mark_current()

    def _prev_hit(self) -> None:
        if self._hits:
            self._hit_idx = (self._hit_idx - 1) % len(self._hits)
            self._mark_current()

    # ---- filtros / ações -------------------------------------------------
    def _cycle_level(self) -> None:
        self._level_idx = (self._level_idx + 1) % len(_LEVELS_CYCLE)
        self.level_btn.set_text(f"Nível: {_LEVELS_CYCLE[self._level_idx][0]}")
        self._render()

    def _open_calendar(self, anchor: tk.Widget, var: tk.StringVar) -> None:
        """Datepicker ancorado no campo (igual à busca das Notas)."""
        s = var.get().strip()
        try:
            cur = datetime.strptime(s, "%d/%m/%Y").date() if len(s) == 10 else None
        except ValueError:
            cur = None
        CalendarPopup(anchor, cur, lambda d: var.set(d.strftime("%d/%m/%Y")))

    def _tick(self) -> None:
        try:
            visible = self.win.state() == "normal"
        except tk.TclError:
            return  # janela destruída (app encerrando) → para o loop
        # tail automático só quando: visível, sem busca ativa e colado no fim
        if visible and self._auto.get() and not self.find_var.get().strip():
            try:
                if self.text.yview()[1] >= 0.999:
                    self._reload()
            except tk.TclError:
                pass
        self.win.after(2000, self._tick)

    def _copy(self) -> None:
        self.win.clipboard_clear()
        self.win.clipboard_append(self.text.get("1.0", "end-1c"))
        self.status.set("Conteúdo exibido copiado para a área de transferência.")

    def _open_folder(self) -> None:
        util.ensure_app_dirs()
        util.open_path(util.LOGS_DIR)

    def _export(self) -> None:
        try:
            rec = self.app.cfg.output.resolved_recordings_dir()
        except Exception:
            rec = None
        try:
            dest = diagnostics.export_zip(rec)
            self.status.set(f"Diagnóstico salvo: {dest}")
            util.open_path(dest.parent)
        except Exception as e:
            log.exception("falha ao exportar diagnóstico")
            self.status.set(f"Falha ao exportar: {e}")

    def show(self) -> None:
        self.win.deiconify()
        self._reload()
        self.win.lift()
        self.win.focus_force()
        enable_dark_titlebar(self.win)


# -- diálogo de crash amigável (#22) -----------------------------------------
# Em vez de morrer/sumir calado no modo sem console, os excepthooks (main.run)
# chamam isto via app.ui() para mostrar o erro com caminho direto p/ log e .zip.
_crash_open = False  # módulo-level: 1 diálogo por vez (não empilha numa rajada de erros)


def show_crash_dialog(app, detail: str) -> None:
    """Diálogo "encontrou um erro" com traceback copiável + 'Abrir log' / 'Exportar
    diagnóstico'. Roda na thread do Tk (via app.ui). TODA a construção é protegida:
    um erro aqui dentro não pode realimentar o excepthook e entrar em loop."""
    global _crash_open
    if _crash_open:
        return  # já há um diálogo aberto — não empilha
    root = getattr(app, "root", None)
    if root is None:
        return  # crash cedo demais (UI ainda não existe) — só ficou no log
    try:
        _crash_open = True
        win = tk.Toplevel(root)
        win.title("ScribaDev — erro inesperado")
        win.configure(bg=_BG)
        win.geometry("680x440")
        win.minsize(480, 320)
        try:
            win.iconbitmap(str(util.ICON_ICO))
        except Exception:
            pass

        def _close():
            global _crash_open
            _crash_open = False
            try:
                win.destroy()
            except tk.TclError:
                pass

        win.protocol("WM_DELETE_WINDOW", _close)

        head = tk.Frame(win, bg=_BG, padx=14, pady=10)
        head.pack(fill="x")
        tk.Label(head, text="O ScribaDev encontrou um erro inesperado", bg=_BG,
                 fg=PALETTE["accent"], font=("Segoe UI", 13, "bold")).pack(anchor="w")
        tk.Label(head, text="O app continua rodando. Se isso atrapalhou uma gravação, exporte o "
                            "diagnóstico e me avise — o detalhe técnico está abaixo.", bg=_BG,
                 fg=PALETTE["muted"], font=("Segoe UI", 9), wraplength=640,
                 justify="left").pack(anchor="w", pady=(2, 0))

        body = tk.Frame(win, bg=_BG, padx=14)
        body.pack(fill="both", expand=True)
        txt = tk.Text(body, bg="#15151c", fg=PALETTE["text"], font=("Consolas", 9), wrap="word",
                      relief="flat", padx=8, pady=6, insertwidth=0)
        txt.insert("1.0", detail)
        txt.configure(state="disabled")  # read-only, mas ainda selecionável/copiável
        txt.pack(fill="both", expand=True, pady=(4, 8))

        def _copy():
            try:
                win.clipboard_clear()
                win.clipboard_append(detail)
            except tk.TclError:
                pass

        def _open_log():
            try:
                app.show_log()
            except Exception:
                log.exception("crash dialog: falha ao abrir o log")

        def _export():
            try:
                rec = app.cfg.output.resolved_recordings_dir()
            except Exception:
                rec = None
            try:
                dest = diagnostics.export_zip(rec)
                util.open_path(dest.parent)
            except Exception:
                log.exception("crash dialog: falha ao exportar diagnóstico")

        bar = tk.Frame(win, bg=_BG, padx=14, pady=10)
        bar.pack(fill="x")
        ModernButton(bar, "Fechar", _close, width=90).pack(side="right")
        ModernButton(bar, "Exportar diagnóstico", _export, kind="primary", width=180).pack(side="right", padx=(0, 8))
        ModernButton(bar, "Abrir log", _open_log, width=100).pack(side="right", padx=(0, 8))
        ModernButton(bar, "Copiar erro", _copy, width=110).pack(side="right", padx=(0, 8))

        win.lift()
        win.focus_force()
        enable_dark_titlebar(win)
    except Exception:
        _crash_open = False
        log.exception("falha ao montar o diálogo de crash")
