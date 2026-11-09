"""Tecla Print Screen no Windows durante a call (#229).

Gancho de teclado de baixo nível (WH_KEYBOARD_LL) que SÓ ESCUTA: toda tecla segue
adiante (CallNextHookEx) e o Print Screen continua fazendo o que o Windows faz
(Ferramenta de Captura, área de transferência). Quando a tecla desce, o app faz a
própria captura da tela e guarda na pasta da gravação. Registrar a tecla com
RegisterHotKey a roubaria do sistema, por isso o gancho.

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


def is_print_down(wparam: int, vk: int) -> bool:
    """A mensagem do gancho é o Print Screen descendo? (puro, testável fora do Windows)"""
    return vk == VK_SNAPSHOT and wparam in (_WM_KEYDOWN, _WM_SYSKEYDOWN)


class _Edge:
    """Um disparo por pressionamento: segurar a tecla gera keydown repetido."""

    def __init__(self):
        self.down = False

    def feed(self, wparam: int, vk: int) -> bool:
        if vk != VK_SNAPSHOT:
            return False
        if wparam in (_WM_KEYUP, _WM_SYSKEYUP):
            self.down = False
            return False
        if is_print_down(wparam, vk) and not self.down:
            self.down = True
            return True
        return False


class PrintKeyHook:
    def __init__(self, callback: Callable[[], None]):
        self.callback = callback
        self.ok = False
        self._tid: int | None = None
        self._ready = threading.Event()
        self._proc = None  # referência viva do callback C (senão o GC o coleta)

    def start(self) -> bool:
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

        edge = _Edge()

        def proc(code, wparam, lparam):
            try:
                if code == 0:
                    kb = ctypes.cast(lparam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                    if edge.feed(int(wparam), int(kb.vkCode)):
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
        log.info("tecla Print Screen observada durante a gravação")
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hook)

    def stop(self) -> None:
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, _WM_QUIT, 0, 0)
            self._tid = None
