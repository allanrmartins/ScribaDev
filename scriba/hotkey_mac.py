"""Atalho global no macOS via Carbon RegisterEventHotKey (ctypes; #104, M7).

Por que Carbon: é a ÚNICA API de hotkey global que não exige permissão
(Accessibility/Input Monitoring). Deprecated há anos, mas presente e usada por
todo app de atalhos até o macOS atual. Plano B (se um dia sumir): CGEventTap
via pyobjc-framework-Quartz + permissão de Monitoramento de Entrada.

Modelo: o handler é instalado UMA vez no dispatcher de eventos e os eventos são
bombeados pelo run loop Cocoa que o Qt já roda na THREAD PRINCIPAL — sem thread
própria (diferente do message loop Win32 do hotkey.py). `register()` DEVE ser
chamado na thread da GUI (main._setup_hotkey já chama).

Keycodes kVK_ANSI_* têm valores próprios (≠ VK do Windows); "cmd" é aceito como
alias de "win" nos specs ("ctrl+alt+r", "cmd+shift+g"...).
"""

from __future__ import annotations

import ctypes
import logging
from typing import Callable

log = logging.getLogger("scriba.hotkey_mac")

_carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")

# modificadores Carbon (Events.h) — nada a ver com os MOD_* do Win32
_MODS = {"ctrl": 0x1000, "alt": 0x0800, "shift": 0x0200, "win": 0x0100, "cmd": 0x0100}

# kVK_ANSI_* (layout físico ANSI; Events.h)
_KEYCODES: dict[str, int] = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06,
    "x": 0x07, "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E,
    "r": 0x0F, "y": 0x10, "t": 0x11, "o": 0x1F, "u": 0x20, "i": 0x22, "p": 0x23,
    "l": 0x25, "j": 0x26, "k": 0x28, "n": 0x2D, "m": 0x2E,
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "5": 0x17, "6": 0x16, "7": 0x1A,
    "8": 0x1C, "9": 0x19, "0": 0x1D,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61,
    "f7": 0x62, "f8": 0x64, "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
}


def parse_mac(spec: str) -> tuple[int, int] | None:
    """"ctrl+alt+r" / "cmd+shift+g" -> (modificadores Carbon, keycode); None se inválido.
    Mesmas regras do parse() do Windows: uma tecla + pelo menos um modificador."""
    mods, key = 0, None
    for token in (spec or "").lower().replace(" ", "").split("+"):
        if token in _MODS:
            mods |= _MODS[token]
        elif token in _KEYCODES:
            if key is not None:
                return None
            key = _KEYCODES[token]
        elif token:
            return None
    if key is None or mods == 0:
        return None
    return mods, key


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_kEventClassKeyboard = 0x6B657962  # 'keyb'
_kEventHotKeyPressed = 5
_kEventParamDirectObject = 0x2D2D2D2D  # '----'
_typeEventHotKeyID = 0x686B6964  # 'hkid'
_SIGNATURE = 0x53434244  # 'SCBD'

_HandlerProc = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

_carbon.GetEventDispatcherTarget.restype = ctypes.c_void_p
_carbon.InstallEventHandler.restype = ctypes.c_int32
_carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, _HandlerProc, ctypes.c_uint32,
                                        ctypes.POINTER(_EventTypeSpec), ctypes.c_void_p, ctypes.c_void_p]
_carbon.RegisterEventHotKey.restype = ctypes.c_int32
_carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _EventHotKeyID,
                                        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
_carbon.UnregisterEventHotKey.restype = ctypes.c_int32
_carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
_carbon.GetEventParameter.restype = ctypes.c_int32
_carbon.GetEventParameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]

_hotkeys: dict[int, tuple[ctypes.c_void_p, Callable[[], None]]] = {}  # id -> (ref, callback)
_next_id = 1
_handler_installed = False


@_HandlerProc
def _on_hotkey(_call_ref, event, _user_data):
    hkid = _EventHotKeyID()
    st = _carbon.GetEventParameter(event, _kEventParamDirectObject, _typeEventHotKeyID,
                                   None, ctypes.sizeof(hkid), None, ctypes.byref(hkid))
    if st == 0 and hkid.signature == _SIGNATURE:
        entry = _hotkeys.get(hkid.id)
        if entry is not None:
            try:
                entry[1]()
            except Exception:
                log.exception("callback da hotkey falhou")
    return 0  # noErr


def _ensure_handler() -> bool:
    global _handler_installed
    if _handler_installed:
        return True
    spec = _EventTypeSpec(_kEventClassKeyboard, _kEventHotKeyPressed)
    st = _carbon.InstallEventHandler(_carbon.GetEventDispatcherTarget(), _on_hotkey,
                                     1, ctypes.byref(spec), None, None)
    if st != 0:
        log.warning("InstallEventHandler falhou (OSStatus=%s)", st)
        return False
    _handler_installed = True
    return True


def register(spec: str, callback: Callable[[], None]) -> int | None:
    """Registra o atalho; devolve o id p/ unregister(), ou None (spec inválido,
    combinação já em uso por outro app, handler indisponível)."""
    global _next_id
    parsed = parse_mac(spec)
    if parsed is None or not _ensure_handler():
        return None
    mods, key = parsed
    hotkey_id = _next_id
    ref = ctypes.c_void_p()
    st = _carbon.RegisterEventHotKey(key, mods, _EventHotKeyID(_SIGNATURE, hotkey_id),
                                     _carbon.GetEventDispatcherTarget(), 0, ctypes.byref(ref))
    if st != 0:
        log.warning("RegisterEventHotKey falhou para '%s' (OSStatus=%s — combinação em uso?)", spec, st)
        return None
    _next_id += 1
    _hotkeys[hotkey_id] = (ref, callback)
    return hotkey_id


def unregister(hotkey_id: int | None) -> None:
    entry = _hotkeys.pop(hotkey_id, None) if hotkey_id is not None else None
    if entry is not None:
        try:
            _carbon.UnregisterEventHotKey(entry[0])
        except Exception:
            log.exception("UnregisterEventHotKey falhou")
