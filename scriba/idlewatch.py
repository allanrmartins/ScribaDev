"""Sentinela de travamento do subprocesso de processamento (#188).

O watchdog do pai (main._process_subprocess, #176) mata um filho que fica 20 min
sem CPU, sem meta.json e sem process.log andando - mas mata por fora, sem saber
ONDE o filho parou. O relato da #188 (diarização parada com CPU a zero, duas
vezes na mesma gravação) chegou sem nenhum rastro da causa: o process.log só
tem o que foi impresso ANTES da trava.

Esta sentinela roda DENTRO do filho: `faulthandler.dump_traceback_later` é uma
thread em C que despeja a pilha de TODAS as threads direto no fd quando o
processo fica `idle_s` sem "bater" - funciona mesmo com o GIL preso num
deadlock, que é exatamente o cenário que interessa. Batimentos: cada escrita no
stdout (as linhas de progresso do process.log), cada mudança de estágio e cada
bloco da diarização. O resumo (espera legítima de rede, sem CPU nem log) fica
SUSPENSO - lá o watchdog do pai já respeita o timeout configurado.

O dump vai para `hang.log` NA PASTA DA REUNIÃO, nunca para o process.log: o
tamanho do process.log é um dos sinais de vida que o pai sonda, e escrever nele
adiaria (ou anularia, com `repeat`) o encerramento do filho travado. Com
`repeat=True` saem dois retratos (8 e 16 min parado) antes do pai agir: pilhas
iguais = travado; diferentes = lentidão extrema. O arquivo só fica na pasta se
houve dump; sem dump ele é removido no desarme.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("scriba.idlewatch")

IDLE_S = 8 * 60          # sem batimento por tanto tempo = despeja as pilhas
HANG_LOG = "hang.log"    # na pasta da reunião (o do app, em logs/, é o da GUI)
_MIN_REARM_S = 1.0       # cada rearme cria uma thread em C; batidas mais miúdas são coalescidas


class _State:
    __slots__ = ("folder", "path", "file", "idle_s", "header_end", "suspended",
                 "last_arm", "prev_stdout")

    def __init__(self, folder: Path, path: Path, file, idle_s: float, header_end: int,
                 prev_stdout) -> None:
        self.folder = folder
        self.path = path
        self.file = file
        self.idle_s = idle_s
        self.header_end = header_end
        self.suspended = False
        self.last_arm = 0.0
        self.prev_stdout = prev_stdout


_lock = threading.Lock()
_state: _State | None = None


class _BeatingStream:
    """Embrulha o stdout: toda escrita é um batimento. Delega o resto ao stream."""

    def __init__(self, stream) -> None:
        self._s = stream

    def write(self, text) -> int:
        beat()
        return self._s.write(text)

    def flush(self) -> None:
        self._s.flush()

    def __getattr__(self, name):
        return getattr(self._s, name)


def _schedule(st: _State) -> None:
    faulthandler.dump_traceback_later(st.idle_s, repeat=True, file=st.file, exit=False)
    st.last_arm = time.monotonic()


def arm(folder, idle_s: float | None = None) -> bool:
    """Liga a sentinela para a reunião em `folder`. Idempotente: já armada para a
    mesma pasta = só um batimento. Nunca levanta (diagnóstico não derruba o pipeline).
    `idle_s` None = IDLE_S lido na hora (ajustável de fora, p/ diagnóstico/E2E)."""
    global _state
    folder = Path(folder)
    if idle_s is None:
        idle_s = IDLE_S
    with _lock:
        if _state is not None:
            if _state.folder == folder:
                _beat_locked(_state)
                return True
            _disarm_locked(None)
        try:
            path = folder / HANG_LOG
            f = open(path, "a", encoding="utf-8", errors="replace")
            try:
                limiar = f"{idle_s / 60:.0f} min" if idle_s >= 60 else f"{idle_s:g} s"
                f.write(f"==== {datetime.now():%d/%m/%Y %H:%M:%S} · sentinela armada "
                        f"(pilhas de todas as threads se {limiar} sem progresso; "
                        f"pid {os.getpid()}) ====\n")
                f.flush()
                header_end = f.tell()
                prev_stdout = sys.stdout
                if prev_stdout is not None and not isinstance(prev_stdout, _BeatingStream):
                    sys.stdout = _BeatingStream(prev_stdout)
                st = _State(folder, path, f, float(idle_s), header_end, prev_stdout)
                _schedule(st)
            except Exception:
                f.close()
                raise
        except Exception:
            log.debug("sentinela de travamento não armou em %s", folder, exc_info=True)
            return False
        _state = st
        return True


def _beat_locked(st: _State) -> None:
    if st.suspended:
        return
    if time.monotonic() - st.last_arm < _MIN_REARM_S:
        return
    try:
        _schedule(st)
    except Exception:
        log.debug("sentinela: rearme falhou", exc_info=True)


def beat() -> None:
    """Sinal de vida (thread-safe, barato quando bate mais de uma vez por segundo)."""
    st = _state
    if st is None:
        return
    with _lock:
        if _state is st:
            _beat_locked(st)


def suspend() -> None:
    """Pausa a sentinela: espera legítima sem CPU nem log (o resumo via rede/CLI)."""
    with _lock:
        st = _state
        if st is None or st.suspended:
            return
        st.suspended = True
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception:
            pass


def resume() -> None:
    """Volta a vigiar depois de `suspend()`."""
    with _lock:
        st = _state
        if st is None or not st.suspended:
            return
        st.suspended = False
        try:
            _schedule(st)
        except Exception:
            log.debug("sentinela: rearme após suspensão falhou", exc_info=True)


def dumped() -> bool:
    """Já houve pelo menos um dump nesta execução?"""
    st = _state
    if st is None:
        return False
    try:
        st.file.flush()
        return st.path.stat().st_size > st.header_end
    except OSError:
        return False


def _disarm_locked(note: str | None) -> None:
    global _state
    st = _state
    if st is None:
        return
    _state = None
    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass
    if isinstance(sys.stdout, _BeatingStream):
        sys.stdout = st.prev_stdout
    houve_dump = False
    try:
        st.file.flush()
        houve_dump = st.path.stat().st_size > st.header_end
        if houve_dump and note:
            st.file.write(f"==== {datetime.now():%d/%m/%Y %H:%M:%S} · {note} ====\n")
    except OSError:
        pass
    try:
        st.file.close()
    except OSError:
        pass
    if not houve_dump:
        try:
            st.path.unlink(missing_ok=True)
        except OSError:
            pass


def disarm(note: str | None = "o processo seguiu depois do dump: lentidão extrema, "
                              "não travamento (o pai não precisou encerrá-lo)") -> None:
    """Desliga a sentinela. Sem dump, o hang.log é removido; com dump, `note`
    é anexada para o leitor saber que o processo NÃO morreu ali."""
    with _lock:
        _disarm_locked(note)
