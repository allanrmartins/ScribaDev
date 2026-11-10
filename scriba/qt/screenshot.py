"""Captura da tela para os prints da call (#229).

Captura só o monitor da janela ATIVA, ou seja, a do app em que o usuário clicou
por último. Com três monitores, um print de todos levava junto o WhatsApp, o
terminal e o que mais estivesse aberto ao lado da call.

- Pela tecla: a janela em foco. Se for do próprio ScribaDev (as Notas, por
  exemplo), vale ela: quem apertou a tecla estava olhando para lá.
- Pela pílula: o clique pode trazer o ScribaDev para frente, então as janelas
  dele são ignoradas e vale a janela de outro app mais à frente.
- Sem como descobrir a janela: o monitor onde está o mouse.

macOS: capturar outros apps exige a permissão de Gravação de Tela. Sem ela o
sistema entrega só o papel de parede e as janelas do próprio app, e um print
assim é pior que nenhum: o print é pulado e a permissão é pedida.

Chamar na thread da GUI (QScreen.grabWindow).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import sys

from PySide6.QtCore import QRect
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QScreen

log = logging.getLogger("scriba.qt.screenshot")


def _cg():
    return ctypes.CDLL(ctypes.util.find_library("CoreGraphics") or
                       "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")


def has_permission() -> bool:
    """macOS: o app pode capturar a tela? (CGPreflightScreenCaptureAccess, sem
    diálogo). Fora do Mac é sempre True."""
    if sys.platform != "darwin":
        return True
    try:
        cg = _cg()
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        return bool(cg.CGPreflightScreenCaptureAccess())
    except Exception:
        log.exception("não consegui consultar a permissão de Gravação de Tela")
        return True  # na dúvida tenta: o pior caso é um print só com o papel de parede


def request_permission() -> None:
    """macOS: pede a permissão de Gravação de Tela (o sistema mostra o diálogo só
    na primeira vez; depois, o usuário liga em Ajustes > Privacidade)."""
    if sys.platform != "darwin":
        return
    try:
        cg = _cg()
        cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
        cg.CGRequestScreenCaptureAccess()
    except Exception:
        log.exception("não consegui pedir a permissão de Gravação de Tela")


def open_privacy_settings() -> None:
    """Abre Ajustes > Privacidade > Gravação de Tela (macOS)."""
    if sys.platform != "darwin":
        return
    import subprocess

    subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security"
                              "?Privacy_ScreenCapture"])


_MIN_SIDE = 100  # janela menor que isso não é "a janela ativa" (ícone, balão, barra)


def pick_screen(window: QRect, screens: list[QRect]) -> int | None:
    """Índice do monitor que contém a maior parte da janela; None se ela não toca
    nenhum. Coordenadas globais do Qt (no Mac, em pontos, iguais às do CoreGraphics)."""
    best, best_area = None, 0
    for i, g in enumerate(screens):
        inter = window.intersected(g)
        area = inter.width() * inter.height() if not inter.isEmpty() else 0
        if area > best_area:
            best, best_area = i, area
    return best


def _mac_active_window(skip_own: bool) -> QRect | None:
    """Retângulo da janela ativa no macOS: a janela normal (camada 0) mais à frente
    do app em primeiro plano. A lista do CoreGraphics vem da frente para trás e
    não precisa de permissão para expor a posição das janelas."""
    import Quartz
    from AppKit import NSWorkspace

    me = os.getpid()
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    front_pid = int(front.processIdentifier()) if front is not None else None
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    normals = []
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:
        b = w.get("kCGWindowBounds") or {}
        if (w.get("kCGWindowLayer", 0) != 0 or float(w.get("kCGWindowAlpha", 1)) <= 0
                or b.get("Width", 0) < _MIN_SIDE or b.get("Height", 0) < _MIN_SIDE):
            continue
        pid = int(w.get("kCGWindowOwnerPID", 0))
        if skip_own and pid == me:
            continue
        normals.append((pid, QRect(int(b["X"]), int(b["Y"]), int(b["Width"]), int(b["Height"]))))
    for pid, rect in normals:  # 1º: a janela do app em foco
        if pid == front_pid:
            return rect
    return normals[0][1] if normals else None  # o foco é nosso (pílula): a de outro app mais à frente


def _win_active_monitor(skip_own: bool) -> str | None:
    """Nome do monitor (\\\\.\\DISPLAYn, o mesmo do QScreen.name()) da janela em
    foco no Windows. Se o foco é do ScribaDev e `skip_own`, sobe a ordem Z até a
    primeira janela visível de outro app."""
    from ctypes import wintypes

    u = ctypes.WinDLL("user32", use_last_error=True)
    HWND = wintypes.HWND
    u.GetForegroundWindow.restype = HWND
    u.GetTopWindow.argtypes = [HWND]
    u.GetTopWindow.restype = HWND
    u.GetWindow.argtypes = [HWND, wintypes.UINT]
    u.GetWindow.restype = HWND
    u.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
    u.IsWindowVisible.argtypes = [HWND]
    u.IsIconic.argtypes = [HWND]
    u.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
    u.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
    u.MonitorFromWindow.argtypes = [HWND, wintypes.DWORD]
    u.MonitorFromWindow.restype = wintypes.HMONITOR

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
    me = os.getpid()

    def pid_of(h) -> int:
        p = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        return p.value

    def cloaked(h) -> bool:  # janela "visível" mas escondida pelo DWM (apps UWP suspensos)
        try:
            v = wintypes.DWORD()
            ctypes.windll.dwmapi.DwmGetWindowAttribute(h, 14, ctypes.byref(v), ctypes.sizeof(v))
            return bool(v.value)
        except Exception:
            return False

    def usable(h) -> bool:
        if not h or not u.IsWindowVisible(h) or u.IsIconic(h) or cloaked(h):
            return False
        if u.GetWindowLongW(h, -20) & 0x80:  # WS_EX_TOOLWINDOW: pílula, balões, barra
            return False
        r = wintypes.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        if r.right - r.left < _MIN_SIDE or r.bottom - r.top < _MIN_SIDE:
            return False
        return not (skip_own and pid_of(h) == me)

    h = u.GetForegroundWindow()
    if not usable(h):
        h = u.GetTopWindow(None)
        while h and not usable(h):
            h = u.GetWindow(h, 2)  # GW_HWNDNEXT: a próxima atrás na ordem Z
    if not h:
        return None
    mi = MONITORINFOEXW()
    mi.cbSize = ctypes.sizeof(mi)
    if not u.GetMonitorInfoW(u.MonitorFromWindow(h, 2), ctypes.byref(mi)):  # NEAREST
        return None
    return mi.szDevice


def active_screen(skip_own: bool = False) -> QScreen | None:
    """O monitor da janela ativa (ver o docstring do módulo)."""
    screens = QGuiApplication.screens()
    if len(screens) <= 1:
        return screens[0] if screens else None
    try:
        if sys.platform == "darwin":
            rect = _mac_active_window(skip_own)
            if rect is not None:
                i = pick_screen(rect, [s.geometry() for s in screens])
                if i is not None:
                    return screens[i]
        elif sys.platform == "win32":
            name = _win_active_monitor(skip_own)
            for s in screens:
                if name and s.name() == name:
                    return s
    except Exception:
        log.exception("não consegui achar a janela ativa; vale o monitor do mouse")
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


def grab_active_screen(skip_own: bool = False) -> QImage | None:
    """Print do monitor da janela ativa, na resolução nativa dele. None se falhar."""
    screen = active_screen(skip_own)
    if screen is None:
        return None
    pm = screen.grabWindow(0)
    return None if pm.isNull() else pm.toImage()
