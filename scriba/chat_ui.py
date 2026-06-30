"""Janela de chat: perguntar à transcrição/resumo de uma reunião já gravada (#22).

O multi-turno é mantido no cliente (concatena o histórico no payload) e despachado
pelo MESMO provider de IA do resumo (ai.complete — claude CLI / Ollama / OpenAI-compat),
então é 100% local quando o provider é local. É só consulta: não altera a nota.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from . import ai
from .widgets import PALETTE, ModernButton, enable_dark_titlebar, make_entry, make_text

_BG = PALETTE["bg"]
_MAX_CONTEXT = 14000  # corta contexto muito longo p/ não estourar a janela/custo do modelo
_TIMEOUT = 180        # claude CLI pode levar dezenas de s; folga sem travar (roda em thread)
_SYSTEM = (
    "Você responde perguntas sobre uma reunião cuja transcrição e resumo são dados a seguir. "
    "Responda em português, de forma direta e fiel ao conteúdo; se a resposta não estiver no "
    "material, diga que não consta na reunião."
)


class ChatWindow:
    def __init__(self, root: tk.Misc, context: str, title: str):
        self._context = (context or "").strip()
        self._truncated = len(self._context) > _MAX_CONTEXT
        if self._truncated:
            self._context = self._context[:_MAX_CONTEXT]
        self._history: list[tuple[str, str]] = []
        self._busy = False

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.title(("Perguntar à reunião — " + (title or "reunião"))[:90])
        self.win.configure(bg=_BG)
        self.win.attributes("-alpha", 0.97)
        self.win.minsize(560, 460)

        body = tk.Frame(self.win, bg=_BG, padx=12, pady=10)
        body.pack(fill="both", expand=True)

        conv_frame = tk.Frame(body, bg=_BG)
        conv_frame.pack(fill="both", expand=True)
        self.conv = make_text(conv_frame, height=16)
        sb = ttk.Scrollbar(conv_frame, orient="vertical", command=self.conv.yview)
        self.conv.configure(yscrollcommand=sb.set, state="disabled")
        self.conv.tag_configure("who", foreground=PALETTE["accent"])
        sb.pack(side="right", fill="y")
        self.conv.pack(side="left", fill="both", expand=True)

        self.status_var = tk.StringVar(value="")
        tk.Label(body, textvariable=self.status_var, bg=_BG, fg=PALETTE["muted"],
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))

        input_row = tk.Frame(body, bg=_BG)
        input_row.pack(fill="x", pady=(4, 0))
        self.q_var = tk.StringVar()
        entry = make_entry(input_row, self.q_var)
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<Return>", lambda e: self._ask())
        self._send_btn = ModernButton(input_row, "Perguntar", self._ask, kind="primary", width=110)
        self._send_btn.pack(side="left", padx=(8, 0))
        self._entry = entry

        intro = "Pergunte sobre esta reunião (ex.: \"o que ficou decidido?\", \"quais as pendências?\")."
        if self._truncated:
            intro += "\n(A reunião é longa — usei só o início do conteúdo como contexto.)"
        self._append("ScribaDev", intro)

    def show(self) -> None:
        self.win.deiconify()
        enable_dark_titlebar(self.win)
        self.win.lift()
        self.win.focus_force()
        self._entry.focus_set()

    # ------------------------------------------------------------------ chat --

    def _ask(self) -> None:
        q = self.q_var.get().strip()
        if not q or self._busy:
            return
        self.q_var.set("")
        self._append("Você", q)
        self._set_busy(True)
        threading.Thread(target=self._worker, args=(q,), daemon=True, name="chat").start()

    def _worker(self, q: str) -> None:
        payload = self._build_payload(q)
        try:
            out = ai.complete(_SYSTEM, payload, timeout=_TIMEOUT, hidden_window=True)
        except Exception:
            out = None
        self.win.after(0, lambda: self._answered(q, out))

    def _build_payload(self, q: str) -> str:
        parts = ["Transcrição e resumo da reunião:", self._context, ""]
        for pq, pa in self._history:
            parts.append(f"Pergunta: {pq}\nResposta: {pa}")
        parts.append(f"Pergunta: {q}\nResposta:")
        return "\n\n".join(parts)

    def _answered(self, q: str, out: str | None) -> None:
        self._set_busy(False)
        if not out:
            self._append("ScribaDev", "(não consegui responder — confira o provedor de IA na aba "
                                       "Resumo das Configurações)")
            return
        self._history.append((q, out))
        self._append("ScribaDev", out)

    def _append(self, who: str, text: str) -> None:
        try:
            self.conv.configure(state="normal")
            self.conv.insert("end", f"{who}: ", ("who",))
            self.conv.insert("end", (text or "").strip() + "\n\n")
            self.conv.configure(state="disabled")
            self.conv.see("end")
        except tk.TclError:
            pass

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.status_var.set("pensando…" if busy else "")
        self._send_btn.set_text("…" if busy else "Perguntar")
