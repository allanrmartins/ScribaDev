"""Tecla de print no Windows durante a call (#229).

Gancho de teclado de baixo nível (WH_KEYBOARD_LL) que SÓ ESCUTA: toda tecla segue
adiante (CallNextHookEx) e a tecla continua fazendo o que já fazia (a Print Screen
abre a Ferramenta de Captura, por exemplo). Quando a tecla configurada desce
(padrão: Print Screen; ver printkey.py), o app faz a própria captura da tela e
guarda na pasta da gravação. Registrar a tecla com RegisterHotKey a roubaria do
sistema, por isso o gancho.

O gancho roda numa thread com loop de mensagens (exigência do Windows) e o
callback precisa ser rápido: só agenda o trabalho na thread da GUI.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger("scriba.printkey")

VK_SNAPSHOT = 0x2C
_WH_KEYBOARD_LL = 13
_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104  # Alt+Print Screen chega como tecla de sistema
_WM_QUIT = 0x0012


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


_WM_KEYUP = 0x0101
_WM_SYSKEYUP = 0x0105


class _Edge:
    """Um disparo por pressionamento da tecla `vk` com exatamente os modificadores
    pedidos. Segurar a tecla gera keydown repetido: só o primeiro conta."""

    def __init__(self, vk: int = VK_SNAPSHOT, mods: frozenset[str] = frozenset()):
        self.vk = vk
        self.mods = mods
        self.down = False

    def feed(self, wparam: int, vk: int, pressed: frozenset[str] = frozenset()) -> bool:
        if vk != self.vk:
            return False
        if wparam in (_WM_KEYUP, _WM_SYSKEYUP):
            self.down = False
            return False
        if wparam in (_WM_KEYDOWN, _WM_SYSKEYDOWN) and not self.down:
            self.down = True
            # tecla sem modificador no config (o padrão, Print Screen): vale com
            # qualquer um (Alt+Print Screen também é print); com modificador, exato
            return not self.mods or pressed == self.mods
        return False


_MOD_VKS = {"ctrl": (0x11,), "alt": (0x12,), "shift": (0x10,), "win": (0x5B, 0x5C)}


def _pressed_mods() -> frozenset[str]:
    get = ctypes.windll.user32.GetAsyncKeyState
    return frozenset(name for name, vks in _MOD_VKS.items() if any(get(v) & 0x8000 for v in vks))


class PrintKeyHook:
    def __init__(self, callback: Callable[[], None], spec: str = "printscreen"):
        from . import printkey

        self.callback = callback
        self.binding = printkey.win_binding(spec)
        self.ok = False
        self._tid: int | None = None
        self._ready = threading.Event()
        self._proc = None  # referência viva do callback C (senão o GC o coleta)

    def start(self) -> bool:
        if self.binding is None:
            log.warning("tecla de print inválida no config; nada a observar")
            return False
        threading.Thread(target=self._loop, daemon=True, name="printkey").start()
        self._ready.wait(timeout=3)
        return self.ok

    def _loop(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        mods, vk = self.binding
        edge = _Edge(vk, mods)

        def proc(code, wparam, lparam):
            try:
                if code == 0:
                    kb = ctypes.cast(lparam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                    pressed = _pressed_mods() if int(kb.vkCode) == vk else frozenset()
                    if edge.feed(int(wparam), int(kb.vkCode), pressed):
                        self.callback()
            except Exception:
                log.exception("gancho do Print Screen falhou (tecla segue normal)")
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._proc = HOOKPROC(proc)
        hook = user32.SetWindowsHookExW(_WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            log.warning("não consegui observar a tecla Print Screen (SetWindowsHookEx falhou)")
            self._ready.set()
            return
        self.ok = True
        self._ready.set()
        log.info("tecla de print observada durante a gravação")
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hook)

    def stop(self) -> None:
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, _WM_QUIT, 0, 0)
            self._tid = None
