"""Sentinela de travamento do subprocesso de processamento (#188).

O watchdog do pai (main._process_subprocess, #176) mata um filho que fica 20 min
sem CPU, sem meta.json e sem process.log andando - mas mata por fora, sem saber
ONDE o filho parou. O relato da #188 (diarização parada com CPU a zero, duas
vezes na mesma gravação) chegou sem nenhum rastro da causa: o process.log só
tem o que foi impresso ANTES da trava.

Esta sentinela roda DENTRO do filho: uma thread daemon (o mesmo desenho do
watchdog da GUI, scriba/watchdog.py) confere a cada instante há quanto tempo o
processo não "bate"; passado `idle_s`, despeja a pilha de TODAS as threads com
`faulthandler.dump_traceback`. Batimentos: cada escrita no stdout (as linhas de
progresso do process.log), cada mudança de estágio e cada bloco da diarização.
O resumo (espera legítima de rede, sem CPU nem log) fica SUSPENSO - lá o
watchdog do pai já respeita o timeout configurado.

Por que NÃO `faulthandler.dump_traceback_later` (#194): a thread em C dele
percorre os frames das outras threads SEM o GIL, enquanto elas executam Python.
No macOS arm64 isso trava a própria thread em C dentro do dump (reproduzido em
CI: thread principal em recursão com exceções no instante do dump) e o
`cancel_dump_traceback_later` seguinte - que todo batimento e o desarme
chamavam - espera por ela para sempre, pendurando o processo inteiro. O dump
síncrono roda COM o GIL: nenhuma outra thread mexe em frames enquanto ele lê.
O que se perde é o retrato de um travamento que segure o GIL em código C; esse
caso segue coberto pelo encerramento do pai, só sem as pilhas.

O dump vai para `hang.log` NA PASTA DA REUNIÃO, nunca para o process.log: o
tamanho do process.log é um dos sinais de vida que o pai sonda, e escrever nele
adiaria (ou anularia) o encerramento do filho travado. Parado de vez, saem
retratos a cada `idle_s` (8 e 16 min) antes do pai agir: pilhas iguais =
travado; diferentes = lentidão extrema. O arquivo só fica na pasta se houve
dump; sem dump ele é removido no desarme.
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
_POLL_S = 1.0            # cadência máxima da conferência (mais fina com idle_s curto)
_JOIN_S = 2.0            # quanto o desarme espera a thread da sentinela encerrar


class _State:
    __slots__ = ("folder", "path", "file", "idle_s", "header_end", "suspended",
                 "last_beat", "prev_stdout", "stop", "thread", "retratos")

    def __init__(self, folder: Path, path: Path, file, idle_s: float, header_end: int,
                 prev_stdout) -> None:
        self.folder = folder
        self.path = path
        self.file = file
        self.idle_s = idle_s
        self.header_end = header_end
        self.suspended = False
        self.last_beat = time.monotonic()
        self.prev_stdout = prev_stdout
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.retratos = 0


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


def _dump_locked(st: _State, parado_s: float) -> None:
    """(thread da sentinela, com _lock e o GIL) Cabeçalho + pilhas de todas as threads."""
    st.retratos += 1
    limiar = f"{parado_s / 60:.0f} min" if parado_s >= 60 else f"{parado_s:.1f} s"
    st.file.write(f"==== {datetime.now():%d/%m/%Y %H:%M:%S} · {limiar} sem progresso "
                  f"(retrato {st.retratos}) ====\n")
    st.file.flush()   # o faulthandler escreve direto no fd: o cabeçalho tem que ir antes
    faulthandler.dump_traceback(file=st.file, all_threads=True)
    st.file.flush()


def _monitor(st: _State) -> None:
    poll = max(0.02, min(_POLL_S, st.idle_s / 4))
    while not st.stop.wait(poll):
        with _lock:
            if _state is not st:
                return
            if st.suspended:
                continue
            parado = time.monotonic() - st.last_beat
            if parado < st.idle_s:
                continue
            try:
                _dump_locked(st, parado)
            except Exception:
                log.debug("sentinela: dump falhou", exc_info=True)
            st.last_beat = time.monotonic()   # próximo retrato só após mais idle_s parado


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
                _state.last_beat = time.monotonic()
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
                st.thread = threading.Thread(target=_monitor, args=(st,), daemon=True,
                                             name="scriba-idlewatch")
                st.thread.start()
            except Exception:
                f.close()
                raise
        except Exception:
            log.debug("sentinela de travamento não armou em %s", folder, exc_info=True)
            return False
        _state = st
        return True


def beat() -> None:
    """Sinal de vida (thread-safe e barato: um timestamp, sem lock nem thread nova)."""
    st = _state
    if st is not None:
        st.last_beat = time.monotonic()


def suspend() -> None:
    """Pausa a sentinela: espera legítima sem CPU nem log (o resumo via rede/CLI)."""
    with _lock:
        st = _state
        if st is not None:
            st.suspended = True


def resume() -> None:
    """Volta a vigiar depois de `suspend()`; o prazo conta a partir daqui."""
    with _lock:
        st = _state
        if st is not None and st.suspended:
            st.suspended = False
            st.last_beat = time.monotonic()


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


def _disarm_locked(note: str | None) -> _State | None:
    global _state
    st = _state
    if st is None:
        return None
    _state = None
    st.stop.set()
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
    return st


def disarm(note: str | None = "o processo seguiu depois do dump: lentidão extrema, "
                              "não travamento (o pai não precisou encerrá-lo)") -> None:
    """Desliga a sentinela. Sem dump, o hang.log é removido; com dump, `note`
    é anexada para o leitor saber que o processo NÃO morreu ali."""
    with _lock:
        st = _disarm_locked(note)
    # o join fica FORA do _lock: a thread precisa dele para perceber o desarme
    if st is not None and st.thread is not None and st.thread is not threading.current_thread():
        st.thread.join(_JOIN_S)
