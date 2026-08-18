"""Watchdog da thread da GUI (pós-mortem do freeze de 2026-07-08).

Um heartbeat (QTimer na thread da GUI) alimenta um monitor que roda em thread própria;
se a GUI ficar mais de `threshold_s` sem bater, o monitor despeja a pilha de TODAS as
threads (faulthandler) em `logs/hang.log` — na próxima vez que o app "não responder",
o culpado fica registrado em vez de virar adivinhação. Um dump por episódio de
travamento; a retomada é logada com a duração.
"""

from __future__ import annotations

import faulthandler
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from . import util

log = logging.getLogger("scriba.watchdog")

_MAX_LOG_BYTES = 2_000_000   # hang.log cresce por append; acima disso recomeça do zero


class GuiWatchdog:
    """`beat()` roda na thread da GUI (via QTimer); o monitor, em daemon-thread.

    `dump` é injetável nos testes (default = _dump_stacks, o faulthandler de verdade).
    `beat`/`check` aceitam `now` explícito p/ teste determinístico (default = monotonic).
    """

    def __init__(self, threshold_s: float = 10.0, poll_s: float = 1.0, dump=None,
                 early_s: float = 5.0):
        self.threshold_s = float(threshold_s)
        self.poll_s = float(poll_s)
        self.early_s = float(early_s)
        self._dump = dump if dump is not None else self._dump_stacks
        self._last_beat = time.monotonic()
        self._hung_since: float | None = None   # início do episódio em curso (já despejado)
        self._early_marked = False   # marcador precoce (#161) já logado neste episódio
        self._stop = threading.Event()

    # -- API ------------------------------------------------------------------

    def beat(self, now: float | None = None) -> None:
        """(thread da GUI) Sinal de vida. Se a GUI trava, isto para de rodar — é o ponto."""
        self._last_beat = time.monotonic() if now is None else now

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="gui-watchdog").start()

    def stop(self) -> None:
        self._stop.set()

    # -- monitor ----------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.wait(self.poll_s):
            try:
                self.check()
            except Exception:
                log.exception("watchdog: check falhou")

    def check(self, now: float | None = None) -> bool:
        """Um passo do monitor (público p/ teste). True se despejou pilhas AGORA.

        Detecção por episódio: despeja UMA vez ao cruzar o limiar e rearma só quando o
        heartbeat volta (senão um travamento longo geraria um dump por segundo)."""
        now = time.monotonic() if now is None else now
        stalled_s = now - self._last_beat
        # marcador PRECOCE (#161): travamento curto finalizado pelo usuário no "não
        # está respondendo" morria antes do dump — esta linha no scriba.log é o rastro
        # mínimo que sobrevive (o incidente de 18/08 ficou sem NENHUM registro nosso)
        if stalled_s > self.early_s and not self._early_marked:
            self._early_marked = True
            log.warning("GUI sem responder há ~%.0fs (marcador precoce; pilhas só em %.0fs)",
                        stalled_s, self.threshold_s)
        if stalled_s > self.threshold_s:
            if self._hung_since is None:
                self._hung_since = self._last_beat
                try:
                    self._dump(stalled_s)
                except Exception:
                    log.exception("watchdog: dump falhou")
                return True
        elif stalled_s <= self.early_s:
            self._early_marked = False   # GUI viva: rearma o marcador p/ o próximo episódio
            if self._hung_since is not None:
                log.warning("GUI voltou a responder após ~%.0fs travada (pilhas em %s)",
                            now - self._hung_since, self.hang_log_path())
                self._hung_since = None
        return False

    # -- dump -------------------------------------------------------------------

    @staticmethod
    def hang_log_path() -> Path:
        return util.LOGS_DIR / "hang.log"

    def _dump_stacks(self, stalled_s: float) -> None:
        """Despeja a pilha de TODAS as threads no hang.log. faulthandler escreve direto
        no fd (à prova de GIL preso em I/O), por isso o flush do cabeçalho antes."""
        path = self.hang_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a"
        try:
            if path.exists() and path.stat().st_size > _MAX_LOG_BYTES:
                mode = "w"
        except OSError:
            pass
        with open(path, mode, encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%d/%m/%Y %H:%M:%S} — GUI sem responder "
                    f"há ~{stalled_s:.0f}s =====\n")
            f.flush()
            faulthandler.dump_traceback(file=f, all_threads=True)
        log.warning("GUI sem responder há ~%.0fs — pilhas de todas as threads em %s",
                    stalled_s, path)
