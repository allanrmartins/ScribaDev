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
_objc.objc_getClass.restype = ctypes.c_void_p
_objc.objc_getClass.argtypes = [ctypes.c_char_p]

# objc_msgSend precisa de protótipo POR ASSINATURA (variádica de verdade não existe
# no ABI arm64): um p/ retorno-ponteiro sem args, um p/ void com um c_ulong e um
# p/ void com um ponteiro (mensagens do tipo `orderFront:nil`).
_send_ptr = ctypes.cast(_objc.objc_msgSend,
                        ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p))
_send_ulong = ctypes.cast(_objc.objc_msgSend,
                          ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong))
_send_void_ptr = ctypes.cast(_objc.objc_msgSend,
                             ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p))
_send_void_bool = ctypes.cast(_objc.objc_msgSend,
                              ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool))

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


def order_front_no_activate(winid: int) -> bool:
    """[[view window] orderFront:nil] — sobe a janela na ordem-Z SEM ativar o app.

    Existe porque o `raise_()` do Qt NÃO serve aqui: o QPA cocoa
    (QCocoaWindow::raise) termina com `[NSApp activateIgnoringOtherApps:YES]`,
    isto é, traz o ScribaDev INTEIRO para a frente e rouba o foco de quem estava
    ativo. A pílula re-assere o topo a cada pulso (600 ms), então durante a
    gravação o foco voltava para cá o tempo todo e era impossível mexer no
    navegador/Meet. `orderFront:` só reordena — não ativa.
    """
    try:
        window = _send_ptr(ctypes.c_void_p(winid), _objc.sel_registerName(b"window"))
        if not window:
            return False
        _send_void_ptr(window, _objc.sel_registerName(b"orderFront:"), None)
        return True
    except Exception:
        log.exception("orderFront: falhou")
        return False


def activate_app() -> bool:
    """[NSApp activateIgnoringOtherApps:YES] — traz o ScribaDev para a frente.

    É o que o Qt fazia SOZINHO em todo `raise_()` (e no raise implícito do
    `show()` de janelas Tool/ToolTip), e que o `QT_MAC_SET_RAISE_PROCESS=0` de
    `qt/__init__.py` desligou. Agora só acontece quando é intenção do usuário —
    abrir uma janela pela bandeja (`widgets.bring_to_front`) — e nunca a reboque
    da pílula de gravação.
    """
    try:
        cls = _objc.objc_getClass(b"NSApplication")
        if not cls:
            return False
        app = _send_ptr(ctypes.c_void_p(cls), _objc.sel_registerName(b"sharedApplication"))
        if not app:
            return False
        _send_void_bool(app, _objc.sel_registerName(b"activateIgnoringOtherApps:"), True)
        return True
    except Exception:
        log.exception("activateIgnoringOtherApps: falhou")
        return False
