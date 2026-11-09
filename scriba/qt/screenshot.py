"""Captura da tela para os prints da call (#229).

Captura TODOS os monitores numa imagem só (como o Print Screen clássico do
Windows): o botão fica na pílula, que costuma morar num monitor diferente do da
call, então escolher "o monitor certo" erraria justo no caso comum.

macOS: capturar outros apps exige a permissão de Gravação de Tela. Sem ela o
sistema entrega só o papel de parede e as janelas do próprio app, e um print
assim é pior que nenhum: `ensure_permission` pede a permissão e o print é pulado.

Chamar na thread da GUI (QScreen.grabWindow).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter

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


def grab_all_screens() -> QImage | None:
    """Uma imagem com todos os monitores, na posição real de cada um (área virtual).
    Com um monitor só, sai na resolução nativa. Com vários, a montagem usa a MENOR
    densidade: juntar três monitores em escala Retina dava 36 megapixels (~1 s só
    para gravar o PNG) para um print que serve de contexto. None se falhar."""
    screens = QGuiApplication.screens()
    if not screens:
        return None
    if len(screens) == 1:
        pm = screens[0].grabWindow(0)
        return None if pm.isNull() else pm.toImage()
    virt = QRect()
    for s in screens:
        virt = virt.united(s.geometry())
    scale = min(s.devicePixelRatio() for s in screens)
    canvas = QImage(int(virt.width() * scale), int(virt.height() * scale), QImage.Format_RGB32)
    canvas.fill(Qt.black)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    try:
        for s in screens:
            pm = s.grabWindow(0)
            if pm.isNull():
                continue
            g = s.geometry()
            target = QRect(int((g.x() - virt.x()) * scale), int((g.y() - virt.y()) * scale),
                           int(g.width() * scale), int(g.height() * scale))
            p.drawPixmap(target, pm)
    finally:
        p.end()
    return canvas
