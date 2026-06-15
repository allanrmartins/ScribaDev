"""Janela "Quantas pessoas participaram?" — pergunta o nº de vozes remotas ao fim
da call para travar a diarização em num_speakers (issue #2).

Sempre no topo, com timeout opcional que cai no modo automático: o processamento
daquela reunião só anda quando o usuário responde, clica "automático" ou o tempo
esgota — nunca trava uma pendência para sempre. A diarização roda só no loopback
("Participantes"), então o número pedido é o de vozes ALÉM do usuário.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import util
from .widgets import PALETTE, ModernButton, enable_dark_titlebar, make_entry

_BG = PALETTE["bg"]


def ask_num_speakers(
    root: tk.Misc,
    on_result: Callable[[int | None], None],
    last_value: int | None = None,
    timeout_seconds: int = 90,
) -> tk.Toplevel:
    """Abre a janela (não-bloqueante) e chama `on_result` EXATAMENTE uma vez:

    - inteiro >= 1  → o usuário informou o nº de participantes remotos;
    - None          → "Não sei / automático", timeout esgotado ou janela fechada.
    """
    win = tk.Toplevel(root)
    win.title("ScribaDev — Participantes")
    win.configure(bg=_BG, padx=22, pady=18)
    win.resizable(False, False)
    win.attributes("-topmost", True)
    try:
        win.iconbitmap(str(util.ICON_ICO))
    except Exception:
        pass

    done = {"called": False}
    timer = {"job": None, "left": int(timeout_seconds or 0)}

    def finish(value: int | None) -> None:
        if done["called"]:
            return
        done["called"] = True
        if timer["job"] is not None:
            try:
                win.after_cancel(timer["job"])
            except Exception:
                pass
        try:
            win.destroy()
        except tk.TclError:
            pass
        on_result(value)

    # -- conteúdo -----------------------------------------------------------
    tk.Label(win, text="Reunião encerrada", bg=_BG, fg=PALETTE["muted"],
             font=("Segoe UI", 9)).pack(anchor="w")
    tk.Label(win, text="Quantas pessoas, além de você, participaram?",
             bg=_BG, fg=PALETTE["text"], font=("Segoe UI", 12, "bold"),
             wraplength=360, justify="left").pack(anchor="w", pady=(2, 2))
    tk.Label(win, text="Conte só as outras vozes (não conte você). Isso ajuda a separar "
                       "os participantes na transcrição.",
             bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
             wraplength=360, justify="left").pack(anchor="w")

    var = tk.StringVar(value=str(last_value) if last_value and last_value >= 1 else "")
    # validação por tecla: só dígitos, até 3 (sanidade — ninguém diariza centenas)
    vcmd = (win.register(lambda P: P == "" or (P.isdigit() and len(P) <= 3)), "%P")
    entry = make_entry(win, var, width=6)
    entry.configure(font=("Segoe UI", 22, "bold"), justify="center",
                    validate="key", validatecommand=vcmd)
    entry.pack(pady=(12, 4), ipady=6)

    def confirm() -> None:
        s = var.get().strip()
        finish(int(s) if s.isdigit() and int(s) >= 1 else None)

    btns = tk.Frame(win, bg=_BG)
    btns.pack(pady=(10, 4))
    ModernButton(btns, "Confirmar", confirm, kind="primary", width=120).pack(side="left", padx=(0, 8))
    ModernButton(btns, "Não sei / automático", lambda: finish(None), width=170).pack(side="left")

    hint_var = tk.StringVar(value="")
    tk.Label(win, textvariable=hint_var, bg=_BG, fg=PALETTE["muted"],
             font=("Segoe UI", 8)).pack(anchor="w", pady=(8, 0))

    # -- atalhos: Enter confirma, Esc/fechar = automático -------------------
    win.bind("<Return>", lambda e: confirm())
    win.bind("<KP_Enter>", lambda e: confirm())
    win.bind("<Escape>", lambda e: finish(None))
    win.protocol("WM_DELETE_WINDOW", lambda: finish(None))

    # -- timeout regressivo (opcional) --------------------------------------
    def tick() -> None:
        if done["called"]:
            return
        if timer["left"] <= 0:
            finish(None)
            return
        hint_var.set(f"Sem resposta em {timer['left']}s → modo automático.")
        timer["left"] -= 1
        timer["job"] = win.after(1000, tick)

    if timeout_seconds and int(timeout_seconds) > 0:
        tick()

    # -- centraliza + foco no topo ------------------------------------------
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w, h = win.winfo_width(), win.winfo_height()
    win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")
    enable_dark_titlebar(win)
    win.lift()
    win.focus_force()
    entry.focus_set()
    entry.select_range(0, "end")
    return win
