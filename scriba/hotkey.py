"""Atalho de teclado global (gravar/parar) via RegisterHotKey — sem dependências."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger("scriba.hotkey")

_MODS = {"ctrl": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012

_VKS: dict[str, int] = {}
_VKS.update({chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)})  # 'a'->0x41...
_VKS.update({str(d): 0x30 + d for d in range(10)})
_VKS.update({f"f{i}": 0x6F + i for i in range(1, 13)})


def parse(spec: str) -> tuple[int, int] | None:
    """"ctrl+alt+r" -> (modificadores, vk); None se inválido."""
    mods, vk = 0, None
    for token in (spec or "").lower().replace(" ", "").split("+"):
        if token in _MODS:
            mods |= _MODS[token]
        elif token in _VKS:
            if vk is not None:
                return None
            vk = _VKS[token]
        elif token:
            return None
    if vk is None or mods == 0:
        return None  # exige ao menos um modificador (evita sequestrar tecla solta)
    return mods, vk


class GlobalHotkey:
    """Registra um atalho global numa thread própria com message loop."""

    def __init__(self, spec: str, callback: Callable[[], None]):
        self.spec = spec
        self.callback = callback
        self.ok = False
        self._tid: int | None = None
        self._mac_id: int | None = None  # id no hotkey_mac (darwin, #104 M7)
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if sys.platform == "darwin":
            # Carbon RegisterEventHotKey (hotkey_mac): eventos bombeados pelo run
            # loop Cocoa do Qt — chamar na thread da GUI (main._setup_hotkey chama)
            from . import hotkey_mac

            self._mac_id = hotkey_mac.register(self.spec, self.callback)
            self.ok = self._mac_id is not None
            if self.ok:
                log.info("hotkey global ativa: %s", self.spec)
            return self.ok
        if sys.platform != "win32":
            # RegisterHotKey é Win32; atalho global Linux vem em marco futuro (#104)
            log.info("hotkey global não suportada neste SO ainda — '%s' ignorado", self.spec)
            return False
        parsed = parse(self.spec)
        if parsed is None:
            return False
        self._thread = threading.Thread(target=self._loop, args=parsed, daemon=True, name="hotkey")
        self._thread.start()
        self._ready.wait(timeout=3)
        return self.ok

    def _loop(self, mods: int, vk: int) -> None:
        user32 = ctypes.windll.user32
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, mods | _MOD_NOREPEAT, vk):
            log.warning("RegisterHotKey falhou para '%s' (combinação já em uso?)", self.spec)
            self._ready.set()
            return
        self.ok = True
        self._ready.set()
        log.info("hotkey global ativa: %s", self.spec)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == _WM_HOTKEY:
                try:
                    self.callback()
                except Exception:
                    log.exception("callback da hotkey falhou")
        user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._mac_id is not None:
            from . import hotkey_mac

            hotkey_mac.unregister(self._mac_id)
            self._mac_id = None
            return
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, _WM_QUIT, 0, 0)
            self._tid = None
