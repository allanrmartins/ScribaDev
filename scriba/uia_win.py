"""Leitura da árvore de acessibilidade (UI Automation) de uma janela (Windows).

Serve para ver, na janela da call do Teams, quem abriu o microfone ou está falando:
os blocos de vídeo e a lista de participantes expõem o estado no Name do elemento
("Fulano, video is on, muted, ..."). Só leitura - nada é clicado nem focado.

Ordem dos métodos conferida na type library do UIAutomationCore.dll (comtypes
GetModule, 2026-10-14). Tudo ctypes puro, como o micusage_win: sem comtypes no
bundle congelado. COM em MTA por thread (UI Automation recomenda MTA ao cliente).

Elementos de apps Chromium (Teams, WebView2) só aparecem depois que algum cliente
de acessibilidade pede a árvore: a primeira leitura de uma janela pode vir quase
vazia. Quem chama trata lista vazia como "ainda não", nunca como erro.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from .micusage_win import _GUID, _call, _ensure_com, _release

_ole32 = ctypes.OleDLL("ole32")
_oleaut32 = ctypes.WinDLL("oleaut32")
_oleaut32.SysFreeString.argtypes = (ctypes.c_void_p,)
_oleaut32.SysFreeString.restype = None

_CLSCTX_INPROC_SERVER = 0x1
_CLSID_CUIAutomation = _GUID.of("FF48DBA4-60EF-4201-AA87-54103EEF594E")
_IID_IUIAutomation = _GUID.of("30CBE57D-D9D0-452A-AB13-7AC5AC4825EE")

# índices na vtable (IUnknown ocupa 0-2)
_ELEMENT_FROM_HANDLE = 6       # IUIAutomation
_CREATE_TRUE_CONDITION = 21    # IUIAutomation
_FIND_ALL = 6                  # IUIAutomationElement
_CURRENT_CONTROL_TYPE = 21     # IUIAutomationElement
_CURRENT_NAME = 23             # IUIAutomationElement
_CURRENT_AUTOMATION_ID = 29    # IUIAutomationElement
_ARRAY_LENGTH, _ARRAY_GET = 3, 4  # IUIAutomationElementArray

_TREE_SCOPE_DESCENDANTS = 4

CONTROL_TYPE_IMAGE = 50006
CONTROL_TYPE_MENU_ITEM = 50011
CONTROL_TYPE_TREE_ITEM = 50024
CONTROL_TYPE_GROUP = 50026

_PTR = ctypes.POINTER(ctypes.c_void_p)
_tls = threading.local()


def _automation() -> ctypes.c_void_p:
    """IUIAutomation da thread (criado uma vez; vive enquanto a thread viver)."""
    uia = getattr(_tls, "uia", None)
    if uia is None:
        _ensure_com()
        uia = ctypes.c_void_p()
        _ole32.CoCreateInstance(ctypes.byref(_CLSID_CUIAutomation), None, _CLSCTX_INPROC_SERVER,
                                ctypes.byref(_IID_IUIAutomation), ctypes.byref(uia))
        _tls.uia = uia
    return uia


def _bstr(el: ctypes.c_void_p, index: int) -> str:
    """Propriedade BSTR do elemento (o BSTR é liberado aqui)."""
    b = ctypes.c_void_p()
    _call(el, index, ctypes.byref(b), argtypes=(_PTR,))
    if not b.value:
        return ""
    try:
        return ctypes.wstring_at(b.value)
    finally:
        _oleaut32.SysFreeString(b)


def _control_type(el: ctypes.c_void_p) -> int:
    ct = ctypes.c_int()
    _call(el, _CURRENT_CONTROL_TYPE, ctypes.byref(ct), argtypes=(ctypes.POINTER(ctypes.c_int),))
    return ct.value


def _descendants(el: ctypes.c_void_p) -> ctypes.c_void_p:
    """IUIAutomationElementArray com todos os descendentes (quem chama libera)."""
    uia = _automation()
    cond = ctypes.c_void_p()
    _call(uia, _CREATE_TRUE_CONDITION, ctypes.byref(cond), argtypes=(_PTR,))
    found = ctypes.c_void_p()
    try:
        _call(el, _FIND_ALL, _TREE_SCOPE_DESCENDANTS, cond, ctypes.byref(found),
              argtypes=(ctypes.c_int, ctypes.c_void_p, _PTR))
    finally:
        _release(cond)
    return found


def _each(array: ctypes.c_void_p, stop: threading.Event | None):
    """Itera os elementos do array (cada um liberado depois do yield)."""
    if not array:
        return
    n = ctypes.c_int()
    _call(array, _ARRAY_LENGTH, ctypes.byref(n), argtypes=(ctypes.POINTER(ctypes.c_int),))
    for i in range(n.value):
        if stop is not None and stop.is_set():
            return
        el = ctypes.c_void_p()
        try:
            _call(array, _ARRAY_GET, i, ctypes.byref(el), argtypes=(ctypes.c_int, _PTR))
            yield el
        except OSError:
            continue  # elemento sumiu entre o FindAll e a leitura
        finally:
            _release(el)


def _window(hwnd: int) -> ctypes.c_void_p:
    el = ctypes.c_void_p()
    _call(_automation(), _ELEMENT_FROM_HANDLE, wintypes.HWND(hwnd), ctypes.byref(el),
          argtypes=(wintypes.HWND, _PTR))
    return el


def descendant_names(hwnd: int, control_types: set[int],
                     stop: threading.Event | None = None) -> list[str]:
    """Names não-vazios dos descendentes da janela com ControlType em `control_types`.

    O Name só é pedido aos elementos do tipo filtrado. Com o chat da reunião aberto
    dentro da janela da call, mensagens podem vir como Group e são lidas também:
    quem chama extrai só o que reconhece e descarta o resto em memória.
    """
    win = _window(hwnd)
    array = ctypes.c_void_p()
    out: list[str] = []
    try:
        array = _descendants(win)
        for el in _each(array, stop):
            try:
                if _control_type(el) in control_types:
                    name = _bstr(el, _CURRENT_NAME)
                    if name:
                        out.append(name)
            except OSError:
                continue
    finally:
        _release(array)
        _release(win)
    return out


def names_under(hwnd: int, automation_id: str, stop: threading.Event | None = None) -> list[str]:
    """Names dos descendentes do primeiro elemento com esse AutomationId.

    Dos outros elementos da janela só o AutomationId é lido: serve para pegar um
    controle conhecido (o avatar do perfil) numa janela que mostra conversas.
    """
    win = _window(hwnd)
    array = ctypes.c_void_p()
    try:
        array = _descendants(win)
        for el in _each(array, stop):
            try:
                if _bstr(el, _CURRENT_AUTOMATION_ID) != automation_id:
                    continue
            except OSError:
                continue
            sub = _descendants(el)
            try:
                return [n for n in (_bstr(c, _CURRENT_NAME) for c in _each(sub, stop)) if n]
            finally:
                _release(sub)
    finally:
        _release(array)
        _release(win)
    return []
