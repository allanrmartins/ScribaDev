"""Uso do microfone por app via process objects do CoreAudio (macOS 14+; #104, M4).

Análogo do ConsentStore do Windows: `kAudioProcessPropertyIsRunningInput == True`
equivale a `LastUsedTimeStop == 0` (app com o mic aberto AGORA). Como o CoreAudio
não guarda histórico, este módulo sintetiza os carimbos start/stop em unidades
FILETIME (100 ns desde 1601) observando as transições entre polls — a máquina de
estados do detector consome `(chave, start, stop)` sem NENHUMA mudança.

Chave = bundle id minúsculo (ex.: "com.microsoft.teams2"); processos múltiplos do
mesmo bundle (helpers do Chrome) são fundidos — mesmo papel da subchave única do
registro no Windows. Processos sem bundle viram "pid<PID>".

Degradações aceitas (documentadas no plano):
- ciclos de mic mais curtos que o poll são invisíveis (na prática o fallback
  wall-clock do detector, detector._resume_or_split, cobre o caso);
- Safari segura o mic via com.apple.WebKit.GPU (sem "safari" no nome) — fora da
  lista default de browsers; fica para depois.

Nenhuma permissão TCC é necessária para esta consulta (validado no spike M2).
Tudo ctypes puro — sem PyObjC neste caminho, que roda a cada poll (2 s).
"""

from __future__ import annotations

import ctypes
import logging

from .mac_tap import _cfstr, _get_prop, _get_size, _SYSTEM_OBJECT
from .micstamp import MicStamps

log = logging.getLogger("scriba.micusage_mac")


def _process_inputs() -> list[tuple[int, str, bool]]:
    """(pid, bundle_id, mic_aberto) para cada process object do CoreAudio."""
    st, size = _get_size(_SYSTEM_OBJECT, "prs#")  # kAudioHardwarePropertyProcessObjectList
    if st != 0 or size == 0:
        return []
    ids = (ctypes.c_uint32 * (size // 4))()
    if _get_prop(_SYSTEM_OBJECT, "prs#", ids) != 0:
        return []
    out: list[tuple[int, str, bool]] = []
    for oid in ids:
        pid_buf = (ctypes.c_int32 * 1)()
        if _get_prop(oid, "ppid", pid_buf) != 0:  # kAudioProcessPropertyPID
            continue
        ref = (ctypes.c_void_p * 1)()
        bundle = _cfstr(ref[0]) if _get_prop(oid, "pbid", ref) == 0 else ""
        inp = (ctypes.c_uint32 * 1)()
        running = _get_prop(oid, "piri", inp) == 0 and bool(inp[0])  # IsRunningInput
        out.append((pid_buf[0], bundle, running))
    return out


# histórico sintetizado (a síntese é comum ao micusage_win: ver micstamp)
_stamps = MicStamps()


def reset() -> None:
    """Zera o histórico (testes)."""
    _stamps.reset()


def snapshot() -> list[tuple[str, int, int]]:
    """(chave, start, stop) por app com histórico de mic — o análogo darwin do
    detector._iter_mic_keys. stop == 0 enquanto o app está com o mic aberto;
    start é reescrito quando o app REABRE o mic (fronteira entre calls, #34)."""
    aberto_por_chave: dict[str, bool] = {}
    for pid, bundle, running in _process_inputs():
        key = (bundle or f"pid{pid}").lower()
        aberto_por_chave[key] = aberto_por_chave.get(key, False) or running
    return _stamps.update(aberto_por_chave)
