"""Janela de chat: perguntar à transcrição/resumo de uma reunião já gravada (#22).

Multi-turno mantido no cliente (concatena o histórico no payload) e despachado pelo
MESMO provider de IA do resumo (ai.complete — claude CLI / Ollama / OpenAI-compat),
então é 100% local quando o provider é local. É só consulta: não altera a nota.

A transcrição INTEIRA nunca vai no prompt. Com a busca ligada (toggle), a IA decide o que
buscar e só os trechos relevantes entram na resposta (self-ask via chat_rag + FTS local); com
ela desligada, responde só do RESUMO. Em ambos os casos o payload carrega apenas as últimas
_MAX_TURNS trocas da conversa.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from . import ai, chat_rag
from .widgets import PALETTE, ModernButton, ToggleSwitch, enable_dark_titlebar, make_entry, make_text

_BG = PALETTE["bg"]
# Histórico da conversa: ACUMULA (cresce a cada troca) até _MAX_TURNS, que é só um teto de
# SEGURANÇA (conversa infinita não estoura o contexto). A partir de _WARN_AFTER_TURNS a UI avisa
# que cada pergunta reenvia todo o histórico (custo cresce) e oferece /clear — o usuário gerencia.
_MAX_TURNS = 50          # teto de segurança do histórico enviado (bem acima do uso real)
_WARN_AFTER_TURNS = 6    # a partir daqui, avisa sobre o custo crescente e oferece /clear
_TIMEOUT = 180           # claude CLI pode levar dezenas de s; folga sem travar (roda em thread)
_CLEAR_CMD = "/clear"
_WARN_MSG = (
    "⚠️ Já são {n} trocas nesta conversa. Daqui pra frente cada nova pergunta reenvia todo o "
    "histórico — então fica gradualmente mais lenta e cara. Digite /clear para zerar o contexto "
    "e recomeçar (o resumo e a busca na transcrição continuam funcionando)."
)
_CLEAR_MSG = (
    "🧹 Contexto limpo — a conversa recomeça do zero. O resumo e a busca na transcrição seguem "
    "disponíveis."
)
# Indicador "pensando" animado, no fluxo da conversa (abaixo da pergunta): a fase muda conforme o
# worker (ex.: "buscando na transcrição") e os pontos animam . → .. → ... → .... → reset.
_THINK_TAG = "think"       # estilo do indicador (accent, itálico) — destaca do texto normal
_THINK_MARK = "think_at"   # marca no Text onde o bloco animado começa
_THINK_INTERVAL = 380      # ms entre os quadros da animação dos pontos

# Papéis das mensagens (estilo Claude Code): minha mensagem num bloco destacado à direita, a
# resposta do ScribaDev como texto limpo à esquerda, avisos do Sistema centralizados e discretos.
_ROLE_USER = "role_user"
_ROLE_ASSIST = "role_assist"
_ROLE_SYS = "role_sys"
_SYSTEM = (
    "Você responde perguntas sobre uma reunião cujo material (resumo e, se incluída, a "
    "transcrição) é dado a seguir. Responda em português, de forma direta e fiel ao "
    "conteúdo; se a resposta não estiver no material, diga que não consta na reunião."
)


class ChatWindow:
    def __init__(self, root: tk.Misc, summary: str, transcript: str | None, title: str):
        self._summary = (summary or "").strip()
        self._transcript = (transcript or "").strip() or None
        self._history: list[tuple[str, str]] = []
        self._busy = False
        from . import config

        self._chat_model = config.load().summary.chat_model or None  # override (ex.: Haiku no chat)
        self._searcher = None  # TranscriptSearcher (FTS :memory:), montado sob demanda
        self._warned = False   # já avisou sobre o custo do histórico? (re-armado pelo /clear)
        self._thinking = False    # indicador "pensando" ativo?
        self._think_after = None  # id do loop de animação (after) p/ cancelar

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
        self.conv = make_text(conv_frame, height=15)
        sb = ttk.Scrollbar(conv_frame, orient="vertical", command=self.conv.yview)
        self.conv.configure(yscrollcommand=sb.set, state="disabled")
        # estilos por papel (bem distintos, à la Claude Code) — sem rótulos "Você:/ScribaDev:"
        self.conv.tag_configure(_ROLE_USER, background="#45454f", foreground=PALETTE["text"],
                                lmargin1=56, lmargin2=56, rmargin=8, spacing1=6, spacing2=2, spacing3=8)
        self.conv.tag_configure(_ROLE_ASSIST, foreground=PALETTE["text"],
                                lmargin1=8, lmargin2=8, rmargin=52, spacing1=4, spacing2=2, spacing3=10)
        self.conv.tag_configure(_ROLE_SYS, foreground=PALETTE["muted"], font=("Segoe UI", 8, "italic"),
                                justify="center", lmargin1=24, lmargin2=24, rmargin=24, spacing1=8, spacing3=8)
        self.conv.tag_configure(_THINK_TAG, foreground=PALETTE["accent"], font=("Consolas", 9, "italic"),
                                lmargin1=8, lmargin2=8, spacing1=4, spacing3=10)
        sb.pack(side="right", fill="y")
        self.conv.pack(side="left", fill="both", expand=True)

        # toggle "buscar na transcrição" — só aparece se a nota tem transcrição. DESLIGADO por
        # padrão: responde só pelo resumo (1 chamada, rápido/barato). Ligado, a IA vasculha a
        # transcrição (FTS) e usa só os TRECHOS relevantes — a transcrição inteira NUNCA vai no
        # prompt (o custo é +1 chamada, o planner; não a fala toda). Detalhe da fala = ligar sob demanda.
        self._include_transcript = tk.BooleanVar(value=False)
        if self._transcript:
            opt = tk.Frame(body, bg=_BG)
            opt.pack(fill="x", pady=(6, 0))
            ToggleSwitch(opt, self._include_transcript).pack(side="left")
            tk.Label(opt, text="buscar na transcrição (vasculha a fala inteira e usa só os trechos relevantes; +1 chamada)",
                     bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8)).pack(side="left", padx=6)

        self.status_var = tk.StringVar(value="")
        tk.Label(body, textvariable=self.status_var, bg=_BG, fg=PALETTE["muted"],
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))

        input_row = tk.Frame(body, bg=_BG)
        input_row.pack(fill="x", pady=(4, 0))
        self.q_var = tk.StringVar()
        self._entry = make_entry(input_row, self.q_var)
        self._entry.pack(side="left", fill="x", expand=True, ipady=4)
        self._entry.bind("<Return>", lambda e: self._ask())
        self._send_btn = ModernButton(input_row, "Perguntar", self._ask, kind="primary", width=110)
        self._send_btn.pack(side="left", padx=(8, 0))

        if self._transcript:
            intro = ("Pergunte sobre esta reunião. Por padrão respondo pelo resumo (rápido). Para "
                     "detalhes que só aparecem na fala, ligue \"buscar na transcrição\" acima — eu "
                     "vasculho a transcrição inteira e uso só os trechos relevantes (nunca mando ela toda).")
        else:
            intro = "Pergunte sobre esta reunião (esta nota não tem transcrição salva; uso o resumo)."
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self._append(intro, _ROLE_ASSIST)

    def _on_close(self) -> None:
        self._stop_thinking()
        if self._searcher is not None:
            self._searcher.close()
            self._searcher = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass

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
        if q.lower() == _CLEAR_CMD:      # comando local — não vai para a IA
            self.q_var.set("")
            self._clear_context()
            return
        self.q_var.set("")
        self._append(q, _ROLE_USER)
        self._set_busy(True)
        self._start_thinking()
        threading.Thread(target=self._worker, args=(q,), daemon=True, name="chat").start()

    def _worker(self, q: str) -> None:
        try:
            if self._transcript and self._include_transcript.get():
                # busca ligada: a IA decide o que buscar, o FTS acha, ela responde só com os
                # trechos (a transcrição inteira nunca vai no prompt).
                out = chat_rag.respond(
                    self._summary, self._history, q,
                    searcher=self._get_searcher(), timeout=_TIMEOUT, model=self._chat_model,
                    status=lambda m: self.win.after(0, lambda m=m: self._set_thinking_phase(m)),
                )
            else:
                out = ai.complete(_SYSTEM, self._summary_payload(q), timeout=_TIMEOUT,
                                  hidden_window=True, model=self._chat_model)
        except Exception:
            out = None
        self.win.after(0, lambda: self._answered(q, out))

    def _get_searcher(self):
        """FTS :memory: sobre a transcrição desta reunião, montado sob demanda (só na 1ª busca).
        None se a nota não tem transcrição."""
        if not self._transcript:
            return None
        if self._searcher is None:
            from .transcript_search import TranscriptSearcher
            self._searcher = TranscriptSearcher(self._transcript)
        return self._searcher

    def _summary_payload(self, q: str) -> str:
        """Payload do caminho SÓ-RESUMO (busca desligada, ou nota sem transcrição). A
        transcrição nunca entra aqui — quando é preciso, ela é consultada via busca (chat_rag)."""
        parts = ["Material da reunião:", self._summary or "(sem resumo)", ""]
        for pq, pa in self._history[-_MAX_TURNS:]:  # só as últimas trocas vão no payload
            parts.append(f"Pergunta: {pq}\nResposta: {pa}")
        parts.append(f"Pergunta: {q}\nResposta:")
        return "\n\n".join(parts)

    def _answered(self, q: str, out: str | None) -> None:
        self._stop_thinking()   # remove o indicador antes de escrever a resposta
        self._set_busy(False)
        if not out:
            self._append("(não consegui responder — confira o provedor de IA na aba "
                         "Resumo das Configurações)", _ROLE_ASSIST)
            return
        self._history.append((q, out))
        self._append(out, _ROLE_ASSIST)
        if len(self._history) >= _WARN_AFTER_TURNS and not self._warned:
            self._warned = True   # avisa 1x ao cruzar o limite (re-armado no /clear)
            self._append(_WARN_MSG.format(n=len(self._history)), _ROLE_SYS)

    def _clear_context(self) -> None:
        """Comando /clear: zera o histórico da conversa (o modelo recomeça do zero) E limpa a tela,
        deixando só a confirmação. NÃO mexe no searcher nem no resumo — busca e resumo seguem."""
        self._stop_thinking()
        self._history.clear()
        self._warned = False
        try:
            self.conv.configure(state="normal")
            self.conv.delete("1.0", "end")   # limpa o texto acima
            self.conv.configure(state="disabled")
        except tk.TclError:
            pass
        self._append(_CLEAR_MSG, _ROLE_SYS)

    def _append(self, text: str, role: str) -> None:
        """Escreve uma mensagem no fluxo, estilizada pelo papel (_ROLE_USER/_ASSIST/_SYS).
        A tag do papel dá cor/margem/alinhamento — quem é quem se lê pelo estilo, sem rótulo."""
        try:
            self.conv.configure(state="normal")
            self.conv.insert("end", (text or "").strip() + "\n", (role,))
            self.conv.configure(state="disabled")
            self.conv.see("end")
        except tk.TclError:
            pass

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.status_var.set("")   # o "pensando" agora é o indicador animado no fluxo da conversa
        self._send_btn.set_text("…" if busy else "Perguntar")

    # --------------------------------------------------- indicador "pensando" --

    def _start_thinking(self) -> None:
        """Mostra o indicador animado logo ABAIXO da pergunta, no fluxo da conversa: a fase
        ('pensando' ou 'buscando na transcrição', via _set_thinking_phase) + pontos que animam
        . → .. → ... → .... → reset. Roda na main thread (o worker está em outra thread)."""
        self._thinking = True
        self._think_dots = 1
        self._think_phase = "pensando"
        try:
            self.conv.configure(state="normal")
            self.conv.mark_set(_THINK_MARK, "end-1c")
            self.conv.mark_gravity(_THINK_MARK, "left")  # fica à esquerda do texto inserido
            self.conv.configure(state="disabled")
        except tk.TclError:
            return
        self._paint_thinking()

    def _set_thinking_phase(self, phase: str) -> None:
        """Troca o texto da fase (os pontos são da animação, então tiramos os '…' da mensagem)."""
        p = (phase or "").strip().rstrip(".… ").strip()
        if p:
            self._think_phase = p

    def _paint_thinking(self) -> None:
        if not self._thinking:
            return
        dots = "." * self._think_dots
        try:
            self.conv.configure(state="normal")
            self.conv.delete(_THINK_MARK, "end-1c")            # apaga o quadro anterior
            self.conv.insert(_THINK_MARK, f"{self._think_phase} {dots}", (_THINK_TAG,))
            self.conv.configure(state="disabled")
            self.conv.see("end")
        except tk.TclError:
            self._think_after = None
            return
        self._think_dots = self._think_dots % 4 + 1            # 1→2→3→4→1 (. .. ... ....)
        self._think_after = self.win.after(_THINK_INTERVAL, self._paint_thinking)

    def _stop_thinking(self) -> None:
        was = self._thinking
        self._thinking = False
        if self._think_after is not None:
            try:
                self.win.after_cancel(self._think_after)
            except Exception:
                pass
            self._think_after = None
        if not was:
            return   # nada para remover (evita apagar conteúdo real com marca velha)
        try:
            self.conv.configure(state="normal")
            self.conv.delete(_THINK_MARK, "end-1c")
            self.conv.configure(state="disabled")
        except tk.TclError:
            pass
