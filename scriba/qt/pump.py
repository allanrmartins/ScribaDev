"""Costura thread -> UI: a fila de callables do `main.py` drenada por um QTimer.

Hoje o `ScribaApp` (tkinter) tem `ui_queue: queue.Queue`, `ui(fn)` que enfileira, e
`_drain_ui` reagendado por `root.after(100, ...)`. Threads de fundo (gravação,
worker de transcrição, updater) NUNCA tocam widgets direto — sempre via `app.ui(fn)`.

Este módulo reproduz EXATAMENTE esse mecanismo em Qt, para que no corte (#53) o
`main.py` só troque `root.after(100, self._drain_ui)` por um `UiPump` — a API
`app.ui(fn)` e todos os `self.ui(...)` espalhados pelo código seguem idênticos.

Por que uma fila drenada por timer, e não signals/slots do Qt? Porque o resto do
app já fala `queue.Queue`; manter a mesma fronteira torna o corte um diff mínimo e
mantém o padrão testável sem um QApplication (a fila é só uma fila).
"""

from __future__ import annotations

import logging
import queue
from typing import Callable

from PySide6.QtCore import QTimer

log = logging.getLogger("scriba.qt.pump")


class UiPump:
    """Drena uma `queue.Queue` de callables na thread da GUI a cada `interval_ms`.

    Uso:
        pump = UiPump()          # cria a fila
        pump.start()             # começa a drenar (precisa de um QApplication vivo)
        pump.ui(lambda: label.setText("oi"))   # de qualquer thread
    """

    def __init__(self, interval_ms: int = 100):
        self.queue: queue.Queue = queue.Queue()
        self._timer = QTimer()
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.drain)

    def ui(self, fn: Callable[[], None]) -> None:
        """Agenda um callable para rodar na thread da GUI. Seguro de qualquer thread."""
        self.queue.put(fn)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def drain(self) -> None:
        """Executa tudo que está na fila agora. Um callable que levante NÃO derruba o
        pump nem os demais da fila (paridade com o _drain_ui tolerante do main.py)."""
        while True:
            try:
                fn = self.queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log.exception("callable da ui_queue levantou")
