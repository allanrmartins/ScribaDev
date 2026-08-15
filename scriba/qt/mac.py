"""Pontes Qt→Cocoa via ctypes/libobjc (#104, M7) — sem PyObjC no caminho da GUI.

No QPA cocoa, `widget.winId()` é o ponteiro do NSView da janela — daí dá para
alcançar o NSWindow e mexer no que o Qt não expõe (hoje: sharingType).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

log = logging.getLogger("scriba.qt.mac")

_objc = ctypes.CDLL(ctypes.util.find_library("objc"))
_objc.sel_registerName.restype = ctypes.c_void_p
_objc.sel_registerName.argtypes = [ctypes.c_char_p]

# objc_msgSend precisa de protótipo POR ASSINATURA (variádica de verdade não existe
# no ABI arm64): um p/ retorno-ponteiro sem args, outro p/ void com um c_ulong.
_send_ptr = ctypes.cast(_objc.objc_msgSend,
                        ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p))
_send_ulong = ctypes.cast(_objc.objc_msgSend,
                          ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong))

_NS_WINDOW_SHARING_NONE = 0


def set_window_sharing_none(winid: int) -> bool:
    """[[view window] setSharingType:NSWindowSharingNone] — a janela some de
    capturas/compartilhamentos mas segue visível localmente (a pílula, #9/#23).

    ATENÇÃO (risco documentado no plano): engines de captura modernas (SCK) podem
    IGNORAR o sharingType em captura de tela inteira — o aceite é manual, numa
    call real. Chamar só com QPA cocoa (widgets.exclude_from_capture guarda)."""
    try:
        window = _send_ptr(ctypes.c_void_p(winid), _objc.sel_registerName(b"window"))
        if not window:
            return False
        _send_ulong(window, _objc.sel_registerName(b"setSharingType:"), _NS_WINDOW_SHARING_NONE)
        return True
    except Exception:
        log.exception("setSharingType falhou")
        return False
