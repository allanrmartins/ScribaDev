"""Sonda de responsividade do shell do Windows (#161).

Incidente real (2026-08-18): `Shell_NotifyIcon` — o que o `QSystemTrayIcon.setIcon`
/`setToolTip` fazem por baixo — é um SendMessage SÍNCRONO para a janela da bandeja
do Explorer. Com o Explorer pendurado, o pulso do ícone REC (a cada 700 ms durante
a gravação) congelou a thread da GUI (evento AppHangXProcB1), o app foi fechado no
"não está respondendo" e uma reunião foi parcialmente perdida.

Este módulo mantém uma daemon-thread que cutuca a janela `Shell_TrayWnd` com
`SendMessageTimeoutW(WM_NULL, …, SMTO_ABORTIFHUNG, 300 ms)` e publica o resultado.
Quem faz chamada COSMÉTICA de shell (pulso da bandeja, tooltip) consulta
`responsive()` antes: shell pendurado → pula a atualização (o ícone congela por
alguns segundos — irrelevante) em vez de congelar o app.

Fail-open por princípio: sonda indisponível, sem dados frescos ou fora do Windows
= `True`. A sonda existe para evitar o travamento, nunca para prender a bandeja.
"""

from __future__ import annotations

import logging
import sys
import threading
import time

log = logging.getLogger("scriba.shellprobe")

_SMTO_ABORTIFHUNG = 0x0002
_TIMEOUT_MS = 300     # teto do custo por sonda (na THREAD da sonda, nunca na GUI)
_INTERVAL_S = 1.0     # cadência da sonda
_MAX_AGE_S = 5.0      # dado mais velho que isto não vale (fail-open)

_SUPPORTED = sys.platform == "win32"   # separado p/ os testes exercitarem a lógica
_started = False
_lock = threading.Lock()
_ok = True
_checked = 0.0


def _find_tray_window() -> int:
    import ctypes

    return int(ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None))


def _send_null_with_timeout(hwnd: int) -> bool:
    import ctypes

    out = ctypes.c_ulong()
    res = ctypes.windll.user32.SendMessageTimeoutW(
        hwnd, 0, 0, 0, _SMTO_ABORTIFHUNG, _TIMEOUT_MS, ctypes.byref(out))
    return bool(res)


def _probe_once() -> bool:
    """Uma sonda. Sem janela de bandeja (sessão de CI etc.) = True: não há Explorer
    p/ pendurar ninguém — e a sonda nunca pode virar o motivo de pular atualização."""
    hwnd = _find_tray_window()
    if not hwnd:
        return True
    return _send_null_with_timeout(hwnd)


def _run() -> None:
    global _ok, _checked
    while True:
        try:
            ok = _probe_once()
        except Exception:   # sonda quebrada não pode prender nada
            log.debug("sonda do shell falhou — assumindo responsivo", exc_info=True)
            ok = True
        _ok = ok
        _checked = time.monotonic()
        time.sleep(_INTERVAL_S)


def ensure_started() -> None:
    """Sobe a daemon-thread da sonda (idempotente; no-op fora do Windows)."""
    global _started
    if not _SUPPORTED:
        return
    with _lock:
        if _started:
            return
        _started = True
        threading.Thread(target=_run, daemon=True, name="shellprobe").start()


def responsive(max_age_s: float = _MAX_AGE_S) -> bool:
    """True = a última sonda viu o shell respondendo (ou não há dado confiável —
    fail-open). É isto que o chamador consulta antes de uma chamada cosmética."""
    if not _SUPPORTED or not _started:
        return True
    if time.monotonic() - _checked > max_age_s:
        return True
    return _ok
