"""Tema escuro do ScribaDev: paleta, botões modernos, toggles e utilidades de janela."""

from __future__ import annotations

import calendar as _calendar
import ctypes
import tkinter as tk
import tkinter.font as tkfont
from datetime import date as _date
from typing import Callable

from . import util

PALETTE = {
    "bg": "#26262e",
    "field": "#33333d",
    "border": "#45454f",
    "text": "#ececf2",
    "muted": "#9b9ba6",
    "accent": "#e54545",
    "accent_hover": "#f05757",
    "accent_press": "#c93a3a",
    "btn": "#3a3a45",
    "btn_hover": "#474753",
    "btn_press": "#2f2f38",
    "ok": "#6cc873",
}

FONT = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 9, "bold")


def enable_dark_titlebar(win: tk.Misc, caption_hex: str = PALETTE["bg"]) -> None:
    """Barra de título escura na cor do fundo (Windows 11; silencioso se indisponível)."""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        if not hwnd:
            return

        def _set(attr: int, value: int) -> None:
            v = ctypes.c_int(value)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

        def _colorref(hex_color: str) -> int:
            r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
            return (b << 16) | (g << 8) | r

        _set(20, 1)  # DWMWA_USE_IMMERSIVE_DARK_MODE
        _set(35, _colorref(caption_hex))  # DWMWA_CAPTION_COLOR
        _set(36, _colorref(PALETTE["text"]))  # DWMWA_TEXT_COLOR
    except Exception:
        pass


def round_rect(c: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return c.create_polygon(pts, smooth=True, **kw)


class ModernButton(tk.Canvas):
    """Botão arredondado com hover/press. kind: "secondary" (cinza) ou "primary" (accent)."""

    def __init__(self, parent, text: str, command: Callable[[], None],
                 kind: str = "secondary", width: int | None = None, height: int = 30):
        bg = parent.cget("bg")
        super().__init__(parent, height=height, bg=bg, highlightthickness=0, cursor="hand2")
        self.command = command
        self._kind = kind
        f = tkfont.Font(family=FONT[0], size=FONT[1])
        # nunca usar self._w: é o caminho Tcl interno do widget no tkinter
        self._bw = width or f.measure(text) + 34
        self._bh = height
        self.configure(width=self._bw)
        self._rect = round_rect(self, 1, 1, self._bw - 1, height - 1, 8, fill=self._color("idle"), outline="")
        fg = "#ffffff" if kind == "primary" else PALETTE["text"]
        self._txt = self.create_text(self._bw // 2, height // 2, text=text, fill=fg, font=FONT)
        self.bind("<Enter>", lambda e: self.itemconfigure(self._rect, fill=self._color("hover")))
        self.bind("<Leave>", lambda e: self.itemconfigure(self._rect, fill=self._color("idle")))
        self.bind("<Button-1>", lambda e: self.itemconfigure(self._rect, fill=self._color("press")))
        self.bind("<ButtonRelease-1>", self._release)

    def _color(self, state: str) -> str:
        p = "accent" if self._kind == "primary" else "btn"
        return PALETTE[p if state == "idle" else f"{p}_{state}"]

    def _release(self, e) -> None:
        self.itemconfigure(self._rect, fill=self._color("hover"))
        if 0 <= e.x <= self._bw and 0 <= e.y <= self._bh:
            self.command()

    def set_text(self, text: str) -> None:
        try:
            self.itemconfigure(self._txt, text=text)
        except tk.TclError:
            pass


class ToggleSwitch(tk.Canvas):
    """Interruptor estilo Windows 11 ligado a uma BooleanVar."""

    W, H = 42, 22

    def __init__(self, parent, variable: tk.BooleanVar):
        super().__init__(parent, width=self.W, height=self.H, bg=parent.cget("bg"),
                         highlightthickness=0, cursor="hand2")
        self.var = variable
        self.var.trace_add("write", lambda *a: self._draw())
        self.bind("<Button-1>", lambda e: self.var.set(not self.var.get()))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        on = bool(self.var.get())
        round_rect(self, 1, 1, self.W - 1, self.H - 1, (self.H - 2) // 2,
                   fill=PALETTE["accent"] if on else PALETTE["border"], outline="")
        x = self.W - self.H // 2 - 1 if on else self.H // 2 + 1
        r = (self.H - 10) // 2
        self.create_oval(x - r, self.H // 2 - r, x + r, self.H // 2 + r, fill="#ffffff", outline="")


class Stepper(tk.Frame):
    """Controle numérico −/+ ligado a uma IntVar."""

    def __init__(self, parent, variable: tk.IntVar, step: int = 5, lo: int = 0, hi: int = 600, suffix: str = " s"):
        super().__init__(parent, bg=parent.cget("bg"))
        self.var, self.step, self.lo, self.hi, self.suffix = variable, step, lo, hi, suffix
        self._txt = tk.StringVar()
        ModernButton(self, "−", lambda: self._bump(-1), width=30, height=26).pack(side="left")
        tk.Label(self, textvariable=self._txt, width=5, bg=self.cget("bg"),
                 fg=PALETTE["text"], font=("Segoe UI", 10, "bold")).pack(side="left", padx=2)
        ModernButton(self, "+", lambda: self._bump(+1), width=30, height=26).pack(side="left")
        variable.trace_add("write", lambda *a: self._sync())
        self._sync()

    def _sync(self) -> None:
        self._txt.set(f"{self.var.get()}{self.suffix}")

    def _bump(self, direction: int) -> None:
        self.var.set(max(self.lo, min(self.hi, self.var.get() + direction * self.step)))


class LinkLabel(tk.Label):
    """Ação discreta em forma de link."""

    def __init__(self, parent, text: str, command: Callable[[], None]):
        super().__init__(parent, text=text, bg=parent.cget("bg"), fg=PALETTE["muted"],
                         cursor="hand2", font=("Segoe UI", 9, "underline"))
        self.bind("<Button-1>", lambda e: command())
        self.bind("<Enter>", lambda e: self.configure(fg=PALETTE["text"]))
        self.bind("<Leave>", lambda e: self.configure(fg=PALETTE["muted"]))


def make_entry(parent, textvariable: tk.StringVar, width: int = 44) -> tk.Entry:
    return tk.Entry(
        parent, textvariable=textvariable, width=width,
        bg=PALETTE["field"], fg=PALETTE["text"], insertbackground=PALETTE["text"],
        relief="flat", font=FONT, highlightthickness=1,
        highlightbackground=PALETTE["border"], highlightcolor=PALETTE["accent"],
    )


def add_placeholder(entry: tk.Entry, var: tk.StringVar, text: str) -> None:
    """Placeholder cinza DENTRO de um make_entry: visível só quando o `var` está
    vazio, some ao digitar. NÃO escreve no `var` — quem o lê (busca/filtros) recebe
    sempre o valor real, nunca o texto do placeholder."""
    ph = tk.Label(entry, text=text, bg=PALETTE["field"], fg=PALETTE["muted"], font=FONT)
    ph.bind("<Button-1>", lambda e: entry.focus_set())  # clicar no hint foca o campo

    def _upd(*_a) -> None:
        if var.get():
            ph.place_forget()
        else:
            ph.place(x=6, rely=0.5, anchor="w")
            ph.lift()

    var.trace_add("write", lambda *a: _upd())
    _upd()


def mask_date_br(entry: tk.Entry, var: tk.StringVar) -> None:
    """Máscara DD/MM/AAAA num campo de data. `var` é o textvariable do `entry`.

    Reformata no <KeyRelease> (DEPOIS que a tecla já entrou) e joga o cursor pro fim:
    fazer isso num trace do var embaralha a digitação, porque o trace dispara NO MEIO
    do insert e briga com o cursor que o Tk ainda vai posicionar. A cor (vermelho se a
    data completa for inválida, ex.: 19/21/8890) fica num trace separado — mudar cor
    não mexe no cursor, então vale p/ digitação, datepicker e limpar."""
    # só dígitos e '/' ENTRAM (validate=key): a letra nem aparece (sem "entra e apaga")
    vcmd = (entry.register(lambda P: P == "" or (len(P) <= 10 and all(c.isdigit() or c == "/" for c in P))), "%P")
    entry.configure(validate="key", validatecommand=vcmd)

    def _reformat(_e=None) -> None:
        s = entry.get()
        new = util.format_date_br(s)
        if new != s:
            entry.configure(validate="none")  # a validação não pode brigar com o reformat
            entry.delete(0, "end")
            entry.insert(0, new)
            entry.configure(validate="key")
        entry.icursor("end")

    def _color(*_a) -> None:
        s = var.get()
        ok = len(s) < 10 or bool(util.date_br_to_iso(s))
        entry.configure(fg=PALETTE["text"] if ok else PALETTE["accent"])

    entry.bind("<KeyRelease>", _reformat)
    var.trace_add("write", lambda *a: _color())


def mask_time_br(entry: tk.Entry, var: tk.StringVar) -> None:
    """Máscara HH:MM num campo de hora: só dígitos ENTRAM (validate=key) e o ':' é
    inserido ao digitar (KeyRelease). Cor vermelha se a hora completa for inválida."""
    vcmd = (entry.register(lambda P: P == "" or (len(P) <= 5 and all(c.isdigit() or c == ":" for c in P))), "%P")
    entry.configure(validate="key", validatecommand=vcmd)

    def _reformat(_e=None) -> None:
        s = entry.get()
        new = util.format_time_hhmm(s)
        if new != s:
            entry.configure(validate="none")
            entry.delete(0, "end")
            entry.insert(0, new)
            entry.configure(validate="key")
        entry.icursor("end")

    def _color(*_a) -> None:
        s = var.get()
        ok = len(s) < 5 or util.time_hhmm_ok(s)
        entry.configure(fg=PALETTE["text"] if ok else PALETTE["accent"])

    entry.bind("<KeyRelease>", _reformat)
    var.trace_add("write", lambda *a: _color())


class CalendarPopup(tk.Toplevel):
    """Mini calendário dark (tkinter puro, sem dependência externa) ancorado a um
    widget. Chama `on_pick(date)` ao escolher um dia. Fecha ao escolher, no Esc ou
    ao perder o foco (clicar fora). Início da semana no domingo (pt-BR)."""

    _WD = ("D", "S", "T", "Q", "Q", "S", "S")
    _MONTHS = ("Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
               "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro")

    def __init__(self, anchor: tk.Widget, initial, on_pick: Callable) -> None:
        super().__init__(anchor.winfo_toplevel())
        self._on_pick = on_pick
        base = initial or _date.today()
        self._y, self._m = base.year, base.month
        self._today = _date.today()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=PALETTE["border"])  # moldura de 1px
        self._body = tk.Frame(self, bg=PALETTE["bg"], padx=8, pady=8)
        self._body.pack(padx=1, pady=1)
        self._build()
        anchor.update_idletasks()
        self.update_idletasks()
        self.geometry(f"+{anchor.winfo_rootx()}+{anchor.winfo_rooty() + anchor.winfo_height() + 3}")
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<FocusOut>", lambda e: self.destroy())  # clicar fora fecha
        self.focus_force()

    def _build(self) -> None:
        for w in self._body.winfo_children():
            w.destroy()
        hdr = tk.Frame(self._body, bg=PALETTE["bg"])
        hdr.pack(fill="x")
        LinkLabel(hdr, "‹", self._prev).pack(side="left")
        tk.Label(hdr, text=f"{self._MONTHS[self._m - 1]} {self._y}", bg=PALETTE["bg"],
                 fg=PALETTE["text"], font=FONT_BOLD).pack(side="left", expand=True)
        LinkLabel(hdr, "›", self._next).pack(side="right")
        grid = tk.Frame(self._body, bg=PALETTE["bg"])
        grid.pack(pady=(6, 0))
        for c, wd in enumerate(self._WD):
            tk.Label(grid, text=wd, bg=PALETTE["bg"], fg=PALETTE["muted"],
                     font=("Segoe UI", 8), width=3).grid(row=0, column=c, padx=1)
        weeks = _calendar.Calendar(firstweekday=6).monthdayscalendar(self._y, self._m)
        for r, week in enumerate(weeks, start=1):
            for c, day in enumerate(week):
                if day == 0:
                    continue
                d = _date(self._y, self._m, day)
                hot = d == self._today
                cell = tk.Label(grid, text=str(day), width=3, font=FONT, cursor="hand2",
                                bg=PALETTE["field"] if hot else PALETTE["bg"], fg=PALETTE["text"])
                cell.grid(row=r, column=c, padx=1, pady=1)
                cell.bind("<Button-1>", lambda e, dd=d: self._pick(dd))
                cell.bind("<Enter>", lambda e, w=cell: w.configure(bg=PALETTE["accent"]))
                cell.bind("<Leave>", lambda e, w=cell, h=hot:
                          w.configure(bg=PALETTE["field"] if h else PALETTE["bg"]))

    def _prev(self) -> None:
        self._m, self._y = (12, self._y - 1) if self._m == 1 else (self._m - 1, self._y)
        self._build()

    def _next(self) -> None:
        self._m, self._y = (1, self._y + 1) if self._m == 12 else (self._m + 1, self._y)
        self._build()

    def _pick(self, d: _date) -> None:
        cb = self._on_pick
        self.destroy()
        cb(d)


def make_secret_entry(parent, textvariable: tk.StringVar, width: int = 44) -> tk.Frame:
    """Campo de senha/chave: tk.Entry mascarado (show="•") + botão olhinho para
    revelar/ocultar. Mesmo tema visual de make_entry. Padrão para TODO campo de
    credencial. Começa sempre mascarado — o estado revelado não persiste, pois
    cada chamada cria um widget novo. O mascaramento é puramente visual: a
    StringVar guarda o valor completo (save/load inalterados)."""
    frame = tk.Frame(parent, bg=PALETTE["bg"])
    entry = tk.Entry(
        frame, textvariable=textvariable, width=width,
        show="•",
        bg=PALETTE["field"], fg=PALETTE["text"], insertbackground=PALETTE["text"],
        relief="flat", font=FONT, highlightthickness=1,
        highlightbackground=PALETTE["border"], highlightcolor=PALETTE["accent"],
    )
    entry.pack(side="left", fill="x", expand=True, ipady=4)

    btn = tk.Button(
        frame, text="👁", relief="flat", cursor="hand2", borderwidth=0,
        bg=PALETTE["field"], fg=PALETTE["muted"],
        activebackground=PALETTE["field"], activeforeground=PALETTE["text"],
        font=("Segoe UI Emoji", 10), takefocus=0,
    )

    def _toggle() -> None:
        revealed = bool(entry.cget("show"))  # mascarado agora -> vamos revelar
        entry.config(show="" if revealed else "•")
        btn.config(text="🔒" if revealed else "👁")

    btn.config(command=_toggle)
    btn.pack(side="left", padx=(4, 0), ipadx=2)
    return frame


def separator(parent) -> tk.Frame:
    return tk.Frame(parent, height=1, bg=PALETTE["border"])


def style_notebook(win: tk.Misc) -> None:
    """Aplica o tema escuro ao ttk.Notebook (abas) e às scrollbars ttk."""
    from tkinter import ttk

    style = ttk.Style(win)
    style.theme_use("clam")
    style.configure("TNotebook", background=PALETTE["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure(
        "TNotebook.Tab",
        background=PALETTE["bg"],
        foreground=PALETTE["muted"],
        padding=(16, 8),
        borderwidth=0,
        font=FONT,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", PALETTE["field"]), ("active", PALETTE["btn"])],
        foreground=[("selected", PALETTE["text"]), ("active", PALETTE["text"])],
    )
    style.configure(
        "Vertical.TScrollbar",
        background=PALETTE["btn"],
        troughcolor=PALETTE["bg"],
        bordercolor=PALETTE["bg"],
        arrowcolor=PALETTE["muted"],
        relief="flat",
    )
    style.map("Vertical.TScrollbar", background=[("active", PALETTE["btn_hover"])])
    style.configure(
        "TCombobox",
        fieldbackground=PALETTE["field"],
        background=PALETTE["btn"],
        foreground=PALETTE["text"],
        arrowcolor=PALETTE["muted"],
        bordercolor=PALETTE["border"],
        lightcolor=PALETTE["field"],
        darkcolor=PALETTE["field"],
        selectbackground=PALETTE["field"],
        selectforeground=PALETTE["text"],
        padding=4,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", PALETTE["field"])],
        foreground=[("readonly", PALETTE["text"])],
    )
    # lista suspensa do combobox (é um Listbox interno)
    win.option_add("*TCombobox*Listbox.background", PALETTE["field"])
    win.option_add("*TCombobox*Listbox.foreground", PALETTE["text"])
    win.option_add("*TCombobox*Listbox.selectBackground", PALETTE["accent"])
    win.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    style.configure(
        "Treeview",
        background=PALETTE["field"],
        fieldbackground=PALETTE["field"],
        foreground=PALETTE["text"],
        borderwidth=0,
        rowheight=26,
        font=FONT,
    )
    style.map(
        "Treeview",
        background=[("selected", PALETTE["accent"])],
        foreground=[("selected", "#ffffff")],
    )


def make_text(parent, **kw) -> tk.Text:
    """tk.Text editável no tema escuro (usado no editor de prompt)."""
    defaults = dict(
        bg=PALETTE["field"], fg=PALETTE["text"], insertbackground=PALETTE["text"],
        relief="flat", font=("Consolas", 9), wrap="word",
        highlightthickness=1, highlightbackground=PALETTE["border"], highlightcolor=PALETTE["accent"],
        padx=8, pady=6, undo=True,
    )
    defaults.update(kw)
    return tk.Text(parent, **defaults)
