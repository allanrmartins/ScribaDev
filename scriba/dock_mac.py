"""Clique no ícone do Dock abre a janela principal (macOS).

O macOS manda o Apple Event "reabrir" (aevt/rapp) a cada clique no Dock e a cada
`open -a` com o app já aberto. O Qt não transforma isso em nada que o app veja:
iniciado só na bandeja (login, --minimized), nenhuma janela existia e o clique
no Dock não fazia nada, nem preview aparecia (2026-10-23).

O handler substitui o do Cocoa para esse evento só; precisa ser instalado na
thread da GUI, depois que o NSApplication terminou de subir (o Cocoa instala o
dele no finishLaunching e sobrescreveria o nosso).
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("scriba.dock_mac")

_handler = None  # referência viva: o NSAppleEventManager não segura o objeto
_on_reopen: Callable[[], None] | None = None
_cls = None  # classe Objective-C criada uma vez só (o nome é global no runtime)


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


def _handler_class():
    global _cls
    if _cls is None:
        from Foundation import NSObject

        class ScribaReopenHandler(NSObject):
            def handleReopen_withReplyEvent_(self, _event, _reply):
                try:
                    if _on_reopen is not None:
                        _on_reopen()
                except Exception:
                    log.exception("falha ao reabrir a janela pelo Dock")

        _cls = ScribaReopenHandler
    return _cls


def install_reopen_handler(on_reopen: Callable[[], None]) -> bool:
    """Chama `on_reopen` (na thread da GUI) a cada clique no ícone do Dock."""
    global _handler, _on_reopen
    try:
        from Foundation import NSAppleEventManager

        _on_reopen = on_reopen
        _handler = _handler_class().alloc().init()
        NSAppleEventManager.sharedAppleEventManager().setEventHandler_andSelector_forEventClass_andEventID_(
            _handler, b"handleReopen:withReplyEvent:", _fourcc("aevt"), _fourcc("rapp"))
        return True
    except Exception:
        log.exception("não consegui tratar o clique no Dock")
        return False
