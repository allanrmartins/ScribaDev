"""Uso do microfone por app via sessões de captura do WASAPI (Windows).

Desde as atualizações de 24-25/09/2026 (KB5124009/KB5124010, 25H2 build 26200.9550)
o Windows deixou de escrever LastUsedTimeStart/Stop no ConsentStore do registro:
o app abre o mic e a chave não muda. O detector, que só olhava o registro, ficou
cego e nenhuma call era detectada.

As sessões de áudio do endpoint de captura (IAudioSessionManager2) seguem
confiáveis: cada processo com o mic aberto tem uma sessão AudioSessionStateActive.
Como a sessão não guarda histórico, os carimbos start/stop são sintetizados pelo
micstamp, exatamente como no macOS - a máquina de estados do detector consome
`(chave, start, stop)` sem mudança.

Chave = caminho do executável com "\\" trocado por "#", o mesmo formato das
subchaves NonPackaged do registro: os patterns por substring ("teams") e o match
de navegador por basename (detector._key_basename) valem sem adaptação.
Processos do mesmo executável (Chrome, Teams) são fundidos: basta um com o mic
aberto.

COM é inicializado por thread (o detector roda em thread própria; o doctor e a
janela principal chamam da thread da GUI, já inicializada pelo Qt). Tudo ctypes
puro - sem comtypes/pycaw, que exigiriam cuidado extra no bundle congelado.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import uuid
from ctypes import wintypes

from .micstamp import MicStamps

log = logging.getLogger("scriba.micusage_win")

_ole32 = ctypes.OleDLL("ole32")
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_CLSCTX_ALL = 0x17
_COINIT_MULTITHREADED = 0x0
_RPC_E_CHANGED_MODE = -2147417850  # 0x80010106: thread já em outro modo (ex.: Qt, STA)
_E_CAPTURE = 1  # EDataFlow.eCapture
_DEVICE_STATE_ACTIVE = 0x1
_SESSION_ACTIVE = 1  # AudioSessionState.AudioSessionStateActive
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    @classmethod
    def of(cls, text: str) -> "_GUID":
        g = cls()
        ctypes.memmove(g.data, uuid.UUID(text).bytes_le, 16)
        return g


_CLSID_MMDeviceEnumerator = _GUID.of("BCDE0395-E52F-467C-8E3D-C4579291692E")
_IID_IMMDeviceEnumerator = _GUID.of("A95664D2-9614-4F35-A746-DE8DB63617E6")
_IID_IAudioSessionManager2 = _GUID.of("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
_IID_IAudioSessionControl2 = _GUID.of("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D")

# índices na vtable (IUnknown ocupa 0-2)
_QUERY_INTERFACE, _RELEASE = 0, 2
_ENUM_AUDIO_ENDPOINTS = 3   # IMMDeviceEnumerator
_COLLECTION_COUNT, _COLLECTION_ITEM = 3, 4  # IMMDeviceCollection
_DEVICE_ACTIVATE = 3        # IMMDevice
_GET_SESSION_ENUMERATOR = 5  # IAudioSessionManager2
_SESSIONS_COUNT, _SESSIONS_GET = 3, 4  # IAudioSessionEnumerator
_SESSION_GET_STATE = 3      # IAudioSessionControl
_SESSION_GET_PROCESS_ID = 14  # IAudioSessionControl2


def _call(obj: ctypes.c_void_p, index: int, *args, argtypes=()):
    """Chama o método `index` da vtable do objeto COM `obj` (HRESULT verificado)."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])(obj, *args)


def _release(obj: ctypes.c_void_p) -> None:
    if obj:
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[_RELEASE])(obj)


_tls = threading.local()


def _ensure_com() -> None:
    """CoInitializeEx uma vez por thread. Thread já inicializada em outro modo
    (a da GUI, STA pelo Qt) serve do mesmo jeito: só as chamadas abaixo importam."""
    if getattr(_tls, "com", False):
        return
    try:
        _ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
    except OSError as e:
        if getattr(e, "winerror", None) != _RPC_E_CHANGED_MODE:
            raise
    _tls.com = True


def _image_path(pid: int) -> str:
    """Caminho completo do executável do processo, ou "" se não der para abrir."""
    h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value
    finally:
        _kernel32.CloseHandle(h)


_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
)
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)


def _device_sessions(device: ctypes.c_void_p) -> list[tuple[int, bool]]:
    """(pid, ativa) de cada sessão de áudio de um endpoint de captura."""
    out: list[tuple[int, bool]] = []
    mgr = ctypes.c_void_p()
    _call(device, _DEVICE_ACTIVATE, ctypes.byref(_IID_IAudioSessionManager2), _CLSCTX_ALL,
          None, ctypes.byref(mgr),
          argtypes=(ctypes.POINTER(_GUID), wintypes.DWORD, ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_void_p)))
    sessions = ctypes.c_void_p()
    try:
        _call(mgr, _GET_SESSION_ENUMERATOR, ctypes.byref(sessions),
              argtypes=(ctypes.POINTER(ctypes.c_void_p),))
        count = ctypes.c_int()
        _call(sessions, _SESSIONS_COUNT, ctypes.byref(count), argtypes=(ctypes.POINTER(ctypes.c_int),))
        for i in range(count.value):
            ctl = ctypes.c_void_p()
            ctl2 = ctypes.c_void_p()
            try:
                _call(sessions, _SESSIONS_GET, i, ctypes.byref(ctl),
                      argtypes=(ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)))
                state = ctypes.c_int()
                _call(ctl, _SESSION_GET_STATE, ctypes.byref(state), argtypes=(ctypes.POINTER(ctypes.c_int),))
                _call(ctl, _QUERY_INTERFACE, ctypes.byref(_IID_IAudioSessionControl2), ctypes.byref(ctl2),
                      argtypes=(ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)))
                pid = wintypes.DWORD()
                try:
                    _call(ctl2, _SESSION_GET_PROCESS_ID, ctypes.byref(pid),
                          argtypes=(ctypes.POINTER(wintypes.DWORD),))
                except OSError:
                    continue  # sessão multiprocesso (sons do sistema): sem dono único
                out.append((pid.value, state.value == _SESSION_ACTIVE))
            except OSError:
                continue  # sessão sumiu entre o enumerar e o ler: ignora só ela
            finally:
                _release(ctl2)
                _release(ctl)
    finally:
        _release(sessions)
        _release(mgr)
    return out


def _capture_sessions() -> list[tuple[int, bool]]:
    """(pid, ativa) de todas as sessões em todos os microfones ativos.

    Recria o enumerador a cada chamada: a lista de sessões de um
    IAudioSessionManager2 antigo não enxerga sessões novas de outros processos.
    """
    _ensure_com()
    enum = ctypes.c_void_p()
    _ole32.CoCreateInstance(ctypes.byref(_CLSID_MMDeviceEnumerator), None, _CLSCTX_ALL,
                            ctypes.byref(_IID_IMMDeviceEnumerator), ctypes.byref(enum))
    devices = ctypes.c_void_p()
    out: list[tuple[int, bool]] = []
    try:
        _call(enum, _ENUM_AUDIO_ENDPOINTS, _E_CAPTURE, _DEVICE_STATE_ACTIVE, ctypes.byref(devices),
              argtypes=(ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)))
        count = wintypes.UINT()
        _call(devices, _COLLECTION_COUNT, ctypes.byref(count), argtypes=(ctypes.POINTER(wintypes.UINT),))
        for i in range(count.value):
            dev = ctypes.c_void_p()
            try:
                _call(devices, _COLLECTION_ITEM, i, ctypes.byref(dev),
                      argtypes=(wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)))
                out.extend(_device_sessions(dev))
            except OSError:
                continue  # microfone desplugado no meio da leitura: os outros valem
            finally:
                _release(dev)
    finally:
        _release(devices)
        _release(enum)
    return out


def _key_for(path: str) -> str:
    """Caminho do exe no formato das subchaves NonPackaged do registro."""
    return path.replace("\\", "#")


# histórico sintetizado (mesma síntese do micusage_mac: ver micstamp)
_stamps = MicStamps()


def reset() -> None:
    """Zera o histórico (testes)."""
    _stamps.reset()


def sessions_by_key() -> dict[str, bool]:
    """{chave: mic aberto agora?} de todo processo com sessão de captura, ativa ou
    não. Uma sessão inativa também conta como "app já usou o mic" (para o doctor)."""
    out: dict[str, bool] = {}
    paths: dict[int, str] = {}
    for pid, ativa in _capture_sessions():
        if not pid:
            continue  # pid 0: sessão do sistema
        if pid not in paths:
            paths[pid] = _image_path(pid)
        if not paths[pid]:
            continue  # processo encerrou (ou é protegido): nada para rastrear
        key = _key_for(paths[pid])
        out[key] = out.get(key, False) or ativa
    return out


def snapshot() -> list[tuple[str, int, int]]:
    """(chave, start, stop) por app com histórico de mic - o análogo WASAPI do
    detector._iter_mic_keys. stop == 0 enquanto o app está com o mic aberto;
    start é reescrito quando o app REABRE o mic (fronteira entre calls, #34).

    Só entra no histórico o app que abriu o mic desde que o ScribaDev subiu: uma
    sessão que nunca esteve ativa não diz nada sobre call.
    """
    atual = sessions_by_key()
    return _stamps.update({k: v for k, v in atual.items() if v or k in _stamps.state})
