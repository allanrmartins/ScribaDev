"""Janela de chat "Perguntar à reunião" em PySide6 (fase B / #48). Porta `scriba/chat_ui.py`.

Maior ganho estético da migração: as respostas do assistente são renderizadas como
markdown NATIVO (QLabel/Qt.MarkdownText — headers, negrito, listas, código, tabelas),
então a `mdview.py` (427 linhas de render à mão) não é mais necessária aqui. Bolhas
estilo Claude Code: minha pergunta num bloco à direita, a resposta como markdown à
esquerda, avisos do sistema centralizados.

Lógica de IA idêntica à versão tk (mesmo `ai.complete` / `chat_rag.respond`, self-ask
com FTS local, histórico multi-turno no cliente, /clear, aviso de custo). O marshaling
worker->UI usa Signals do Qt (thread-safe) em vez do `after(0, ...)` do Tk.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import ai, chat_rag
from . import theme, widgets

log = logging.getLogger("scriba.qt.chat_ui")

_MAX_TURNS = 50
_WARN_AFTER_TURNS = 6
_TIMEOUT = 180
_CLEAR_CMD = "/clear"
_WARN_MSG = (
    "⚠️ Já são {n} trocas nesta conversa. Daqui pra frente cada nova pergunta reenvia todo o "
    "histórico — fica gradualmente mais lenta e cara. Digite /clear para zerar o contexto "
    "(o resumo e a busca na transcrição continuam funcionando)."
)
_CLEAR_MSG = ("🧹 Contexto limpo — a conversa recomeça do zero. O resumo e a busca na transcrição "
             "seguem disponíveis.")
_THINK_INTERVAL = 380
_SYSTEM = (
    "Você responde perguntas sobre uma reunião cujo material (resumo e, se incluída, a "
    "transcrição) é dado a seguir. Responda em português, de forma direta e fiel ao "
    "conteúdo; se a resposta não estiver no material, diga que não consta na reunião."
)


class ChatWindow(QWidget):
    # marshaling worker(thread) -> UI (GUI thread), thread-safe
    _answered_sig = Signal(str, object)   # (pergunta, resposta|None)
    _phase_sig = Signal(str)              # fase do "pensando" (ex.: "buscando na transcrição")

    def __init__(self, summary: str, transcript: str | None, title: str):
        super().__init__()
        self._summary = (summary or "").strip()
        self._transcript = (transcript or "").strip() or None
        self._history: list[tuple[str, str]] = []
        self._busy = False
        self._warned = False
        self._searcher = None
        self._bubbles: list[QLabel] = []
        from .. import config

        self._chat_model = config.load().summary.chat_model or None
        self._think_lbl: QLabel | None = None
        self._think_dots = 1
        self._think_phase = "pensando"

        self.setWindowTitle(("Perguntar à reunião — " + (title or "reunião"))[:90])
        self.setMinimumSize(560, 460)
        self.setWindowOpacity(0.98)
        widgets.remember_geometry(self, "qt_chat", default=(240, 150, 640, 620))

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        # área de conversa (scroll + bolhas)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._conv = QWidget()
        self._conv_lay = QVBoxLayout(self._conv)
        self._conv_lay.setContentsMargins(2, 2, 2, 2)
        self._conv_lay.setSpacing(8)
        self._conv_lay.addStretch(1)  # empurra as mensagens p/ cima; novas entram antes deste
        self._scroll.setWidget(self._conv)
        root.addWidget(self._scroll, 1)

        # toggle "buscar na transcrição" (só se a nota tem transcrição)
        self._toggle = None
        if self._transcript:
            opt = QHBoxLayout()
            self._toggle = widgets.ToggleSwitch(checked=False)
            hint = QLabel("buscar na transcrição (mais preciso; +1 chamada)")
            hint.setProperty("role", "muted")
            hint.setStyleSheet(f"font-size:{theme.active().font_size_small}pt;")
            opt.addWidget(self._toggle)
            opt.addWidget(hint)
            opt.addStretch(1)
            root.addLayout(opt)

        # entrada
        input_row = QHBoxLayout()
        self._entry = widgets.make_entry("Pergunte sobre esta reunião…")
        self._entry.returnPressed.connect(self._ask)
        self._send = widgets.ModernButton("Perguntar", self._ask, kind="primary")
        input_row.addWidget(self._entry, 1)
        input_row.addWidget(self._send)
        root.addLayout(input_row)

        self._answered_sig.connect(self._answered)
        self._phase_sig.connect(self._set_phase)

        if self._transcript:
            intro = ("Pergunte sobre esta reunião. Por padrão respondo pelo resumo (rápido). Para "
                     "detalhes que só aparecem na fala, ligue \"buscar na transcrição\" acima — eu "
                     "vasculho a transcrição inteira e uso só os trechos relevantes.")
        else:
            intro = "Pergunte sobre esta reunião (esta nota não tem transcrição salva; uso o resumo)."
        self._append_assistant(intro)

    def show(self) -> None:  # noqa: A003
        super().show()
        self.raise_()
        self.activateWindow()
        widgets.enable_dark_titlebar(self)
        self._entry.setFocus()

    def closeEvent(self, event) -> None:
        self._stop_thinking()
        if self._searcher is not None:
            self._searcher.close()
            self._searcher = None
        super().closeEvent(event)

    # ------------------------------------------------------------- bolhas ------

    def _add_row(self, widget: QWidget, align: str) -> None:
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        if align == "right":
            rl.addStretch(1)
            rl.addWidget(widget)
        elif align == "center":
            rl.addStretch(1)
            rl.addWidget(widget)
            rl.addStretch(1)
        else:
            rl.addWidget(widget)
            rl.addStretch(1)
        self._conv_lay.insertWidget(self._conv_lay.count() - 1, row)  # antes do stretch final
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _bubble_max(self) -> int:
        """Largura máxima das bolhas: acompanha a janela (~82% do viewport) em vez de um
        valor fixo, p/ a conversa usar a largura toda e não ficar espremida num canto."""
        w = self._scroll.viewport().width() if self._scroll is not None else self.width()
        return max(300, int(w * 0.82))

    def _bubble(self, text: str, *, markdown: bool) -> QLabel:
        lbl = QLabel()
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        lbl.setOpenExternalLinks(True)
        if markdown:
            lbl.setTextFormat(Qt.MarkdownText)   # render nativo (mata a mdview.py)
        lbl.setText(text)
        lbl.setMaximumWidth(self._bubble_max())
        self._bubbles.append(lbl)
        return lbl

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        mx = self._bubble_max()
        for b in list(self._bubbles):
            try:
                b.setMaximumWidth(mx)
            except RuntimeError:
                self._bubbles.remove(b)   # bolha já destruída (após /clear)

    def _append_user(self, text: str) -> None:
        t = theme.active()
        lbl = self._bubble(text, markdown=False)
        lbl.setStyleSheet(
            f"background:{theme._rgba(t.accent, 0.20)}; border:1px solid {theme._rgba(t.accent, 0.42)};"
            f" border-radius:13px; padding:10px 14px; color:{t.text}; font-size:{t.font_size + 2}pt;"
        )
        self._add_row(lbl, "right")

    def _append_assistant(self, md: str) -> None:
        t = theme.active()
        lbl = self._bubble(md, markdown=True)
        lbl.setStyleSheet(
            f"background:{theme._rgba(t.surface, 0.60)}; border:1px solid {theme._rgba(t.border, 0.55)};"
            f" border-radius:13px; padding:10px 14px; color:{t.text}; font-size:{t.font_size + 2}pt;"
        )
        self._add_row(lbl, "left")

    def _append_system(self, text: str) -> None:
        t = theme.active()
        lbl = self._bubble(text, markdown=False)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color:{t.muted}; font-size:{t.font_size + 1}pt; font-style:italic; padding:4px;")
        self._add_row(lbl, "center")

    # -------------------------------------------------------------- chat -------

    def _ask(self) -> None:
        q = self._entry.text().strip()
        if not q or self._busy:
            return
        if q.lower() == _CLEAR_CMD:
            self._entry.clear()
            self._clear_context()
            return
        self._entry.clear()
        self._append_user(q)
        self._set_busy(True)
        self._start_thinking()
        threading.Thread(target=self._worker, args=(q,), daemon=True, name="chat").start()

    def _worker(self, q: str) -> None:
        try:
            if self._transcript and self._toggle is not None and self._toggle.isChecked():
                out = chat_rag.respond(
                    self._summary, self._history, q,
                    searcher=self._get_searcher(), timeout=_TIMEOUT, model=self._chat_model,
                    status=lambda m: self._phase_sig.emit(m),
                )
            else:
                out = ai.complete(_SYSTEM, self._summary_payload(q), timeout=_TIMEOUT,
                                  hidden_window=True, model=self._chat_model)
        except Exception:
            out = None
        self._answered_sig.emit(q, out)

    def _get_searcher(self):
        if not self._transcript:
            return None
        if self._searcher is None:
            from ..transcript_search import TranscriptSearcher

            self._searcher = TranscriptSearcher(self._transcript)
        return self._searcher

    def _summary_payload(self, q: str) -> str:
        parts = ["Material da reunião:", self._summary or "(sem resumo)", ""]
        for pq, pa in self._history[-_MAX_TURNS:]:
            parts.append(f"Pergunta: {pq}\nResposta: {pa}")
        parts.append(f"Pergunta: {q}\nResposta:")
        return "\n\n".join(parts)

    def _answered(self, q: str, out) -> None:
        self._stop_thinking()
        self._set_busy(False)
        if not out:
            self._append_assistant("(não consegui responder — confira o provedor de IA na aba "
                                    "Resumo das Configurações)")
            return
        self._history.append((q, out))
        self._append_assistant(out)
        if len(self._history) >= _WARN_AFTER_TURNS and not self._warned:
            self._warned = True
            self._append_system(_WARN_MSG.format(n=len(self._history)))

    def _clear_context(self) -> None:
        self._stop_thinking()
        self._history.clear()
        self._warned = False
        while self._conv_lay.count() > 1:  # mantém só o stretch final
            child = self._conv_lay.takeAt(0)
            w = child.widget()
            if w is not None:
                w.deleteLater()
        self._bubbles.clear()
        self._append_system(_CLEAR_MSG)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._send.setText("…" if busy else "Perguntar")

    # ---------------------------------------------- indicador "pensando" -------

    def _start_thinking(self) -> None:
        self._think_dots = 1
        self._think_phase = "pensando"
        self._think_lbl = QLabel()
        self._think_lbl.setStyleSheet(f"color:{theme.active().accent}; font-style:italic; padding:2px 8px;")
        self._add_row(self._think_lbl, "left")
        self._think_timer = QTimer(self)
        self._think_timer.timeout.connect(self._paint_thinking)
        self._think_timer.start(_THINK_INTERVAL)
        self._paint_thinking()

    def _set_phase(self, phase: str) -> None:
        p = (phase or "").strip().rstrip(".… ").strip()
        if p:
            self._think_phase = p

    def _paint_thinking(self) -> None:
        if self._think_lbl is None:
            return
        self._think_lbl.setText(f"{self._think_phase} {'.' * self._think_dots}")
        self._think_dots = self._think_dots % 4 + 1

    def _stop_thinking(self) -> None:
        if getattr(self, "_think_timer", None) is not None:
            self._think_timer.stop()
            self._think_timer = None
        if self._think_lbl is not None:
            row = self._think_lbl.parentWidget()
            self._think_lbl = None
            if row is not None:
                row.deleteLater()


# --------------------------------------------------------------- harness ------

def main() -> int:
    import sys

    from PySide6.QtWidgets import QApplication

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = QApplication(sys.argv)
    theme.apply(app)

    summary = ("## Resumo da reunião\n\n"
               "- Boleto **não gera** em produção com CNPJ alfanumérico.\n"
               "- Falta o de/para de centros.\n\n"
               "| Item | Responsável |\n|---|---|\n| Boleto | Funcional |\n| Centros | Funcional |")
    win = ChatWindow(summary, transcript="linha 1 da fala\nlinha 2 da fala", title="Boleto (demo)")
    win.resize(620, 560)
    win.show()
    # demonstra as bolhas sem depender do provedor de IA
    win._append_user("Quais foram os bloqueios?")
    win._append_assistant("Foram **dois**:\n\n1. Boleto em produção (CNPJ alfanumérico)\n"
                          "2. De/para de centros\n\nAmbos dependem do time *funcional*.")
    return app.exec()


if __name__ == "__main__":
    import os

    rc = main()
    logging.shutdown()
    os._exit(rc)
