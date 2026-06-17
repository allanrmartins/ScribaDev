"""Janela "Quantas pessoas participaram?" — pergunta o nº de vozes remotas ao fim
da call para travar a diarização em num_speakers (issue #2).

Sempre no topo, com timeout opcional que cai no modo automático: o processamento
daquela reunião só anda quando o usuário responde, clica "automático" ou o tempo
esgota — nunca trava uma pendência para sempre. A diarização roda só no loopback
("Participantes"), então o número pedido é o de vozes ALÉM do usuário.
"""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from typing import Callable

from . import util
from .widgets import FONT, FONT_BOLD, PALETTE, ModernButton, enable_dark_titlebar, make_entry

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


def has_labelable_voices(folder) -> bool:
    """True se a pasta da gravação tem vozes (voices.json) para rotular (#1)."""
    try:
        voices = json.loads((Path(folder) / "voices.json").read_text(encoding="utf-8"))
        return bool(voices)
    except (OSError, ValueError):
        return False


def label_speakers_dialog(parent: tk.Misc, folder, on_saved: Callable[[], None] | None = None,
                          guesses: dict[str, str] | None = None) -> tk.Toplevel:
    """Diálogo "Rotular participantes" (#1): nomeia as vozes de uma reunião.

    Lê os embeddings de voices.json (na pasta da gravação), deixa o usuário dar
    um nome a cada voz e, ao salvar, aprende a voz (futuro) E corrige a nota atual
    via notes.relabel_speakers. Vozes já reconhecidas vêm preenchidas para confirmar.
    """
    folder = Path(folder)
    try:
        voices: dict = json.loads((folder / "voices.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        voices = {}

    win = tk.Toplevel(parent)
    win.title("ScribaDev — Rotular participantes")
    win.configure(bg=_BG, padx=20, pady=16)
    win.resizable(False, False)
    win.transient(parent if isinstance(parent, (tk.Tk, tk.Toplevel)) else None)
    try:
        win.iconbitmap(str(util.ICON_ICO))
    except Exception:
        pass

    if not voices:
        tk.Label(win, text="Esta reunião não tem vozes separadas para rotular.",
                 bg=_BG, fg=PALETTE["text"], font=FONT, wraplength=320).pack(pady=(0, 12))
        ModernButton(win, "Fechar", win.destroy, kind="primary", width=100).pack()
        enable_dark_titlebar(win)
        win.lift()
        win.focus_force()
        return win

    tk.Label(win, text="Rotular participantes", bg=_BG, fg=PALETTE["text"],
             font=("Segoe UI", 12, "bold")).pack(anchor="w")
    tk.Label(win, text="Revise o nome de cada voz e MARQUE o check só nas que tiver certeza — "
                       "só as marcadas são salvas. O palpite da IA vem desmarcado: confirme, "
                       "corrija o nome ou deixe desmarcado se não souber. O ScribaDev aprende as "
                       "marcadas e corrige esta nota na hora.",
             bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
             wraplength=380, justify="left").pack(anchor="w", pady=(2, 10))

    rows: list[tuple[str, tk.StringVar, tk.BooleanVar]] = []
    guesses = guesses or {}
    for label, info in voices.items():
        row = tk.Frame(win, bg=_BG)
        row.pack(fill="x", pady=3)
        slabel = str(label)
        info = info or {}
        auto = bool(info.get("auto"))
        labeled = bool(info.get("labeled"))   # você já rotulou esta voz antes
        anon = slabel.startswith("Participante ")
        guess = (guesses.get(slabel) or "").strip()
        # pré-preenche: voz já nomeada vem com o nome; "Participante N" vem com o
        # palpite da IA (se houver) p/ só confirmar; senão, vazia.
        prefill = guess or ("" if anon else slabel)
        # check por voz: já reconhecida/rotulada nasce MARCADA; palpite e vazio
        # nascem DESMARCADOS — no Salvar só vale o que você marcou.
        confirmed = tk.BooleanVar(value=bool(auto or labeled))
        # legenda DESMARCADO = o motivo do palpite; MARCADO = "Confirmado" (verde)
        if auto:
            base = "reconhecido"
        elif labeled:
            base = "você rotulou"
        elif anon and guess:
            base = "palpite"
        else:
            base = "confirmar"
        tk.Label(row, text=slabel, bg=_BG, fg=PALETTE["text"], font=FONT_BOLD,
                 width=16, anchor="w").pack(side="left")
        chk = tk.Checkbutton(
            row, variable=confirmed, bg=_BG, font=("Segoe UI", 8),
            activebackground=_BG, selectcolor=PALETTE["field"],
            bd=0, highlightthickness=0, anchor="w", takefocus=False,
        )
        chk.pack(side="right", padx=(6, 0))

        # texto + cor seguem o check: marcado → "Confirmado" verde; senão, motivo cinza.
        # default-args fixam o trio desta linha (evita o late-binding do loop).
        def _sync_chk(*_a, _chk=chk, _base=base, _var=confirmed):
            on = _var.get()
            color = PALETTE["ok"] if on else PALETTE["muted"]
            _chk.config(text=("Confirmado" if on else _base), fg=color, activeforeground=color)
        _sync_chk()
        confirmed.trace_add("write", _sync_chk)

        var = tk.StringVar(value=prefill)
        make_entry(row, var, width=20).pack(side="left", fill="x", expand=True, ipady=3)
        rows.append((slabel, var, confirmed))

    def save() -> None:
        # só vozes MARCADAS com nome != label entram; desmarcado nunca é salvo,
        # mesmo com palpite preenchido (você não confirmou).
        renames = {label: var.get().strip() for label, var, confirmed in rows
                   if confirmed.get() and var.get().strip() and var.get().strip() != label}
        if renames:
            from . import notes

            try:
                notes.relabel_speakers(folder, renames)
            except Exception:
                import logging

                logging.getLogger("scriba.speakers_ui").exception("falha ao rotular vozes")
        win.destroy()
        if on_saved is not None:
            on_saved()

    btns = tk.Frame(win, bg=_BG)
    btns.pack(fill="x", pady=(14, 0))
    ModernButton(btns, "Salvar", save, kind="primary", width=110).pack(side="right")
    ModernButton(btns, "Cancelar", win.destroy, width=100).pack(side="right", padx=(0, 8))

    win.update_idletasks()
    enable_dark_titlebar(win)
    win.lift()
    win.focus_force()
    return win
