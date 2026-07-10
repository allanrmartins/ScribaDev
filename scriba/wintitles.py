"""Títulos das janelas visíveis de um conjunto de processos (Win32/ctypes).

Usado pela detecção de calls em navegador: o registro do mic só diz que
"chrome.exe está com o microfone aberto" — é o título de alguma janela do
navegador (a aba ativa) que diz SE aquilo é uma reunião (Meet, Teams web...).

À prova de travamento (#114): `EnumWindows` pode BLOQUEAR indefinidamente quando
algum processo com janela top-level está travado segurando locks do window manager
(aconteceu em produção: pendurou o thread do detector dentro do início da gravação
e congelou o app inteiro). Título de janela é sempre cosmético/best-effort, então
a enumeração roda numa thread própria com prazo: estourou, devolve lista vazia e
segue a vida — a thread presa é abandonada (daemon) e nenhuma nova enumeração é
empilhada enquanto ela não morrer.
"""

from __future__ import annotations

import logging
import sys
import threading

log = logging.getLogger("scriba.wintitles")

# Prazo da enumeração. Normalmente ela leva poucos ms; segundos = algo travado.
_ENUM_TIMEOUT_S = 1.5

# Threads de enumeração abandonadas por timeout que ainda não morreram. Enquanto
# houver uma viva, novas chamadas devolvem [] direto — sem empilhar uma thread
# presa a cada poll do detector (2 s) durante um travamento longo do sistema.
_stuck_lock = threading.Lock()
_stuck: list[threading.Thread] = []

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def _exe_of_pid(pid: int) -> str:
        """Basename minúsculo do executável do processo ('' se inacessível)."""
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return ""
            return buf.value.rsplit("\\", 1)[-1].lower()
        finally:
            _kernel32.CloseHandle(handle)

    def _enum_titles(exe_names: set[str]) -> list[str]:
        """Enumeração crua (pode bloquear — só chamar via window_titles).

        exe_names: basenames minúsculos com extensão (ex.: {"chrome.exe"}).
        """
        titles: list[str] = []
        exe_cache: dict[int, str] = {}

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _on_window(hwnd, _lparam):
            # filtros baratos primeiro; OpenProcess só para janelas com título
            if not _user32.IsWindowVisible(hwnd):
                return True
            length = _user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe = exe_cache.get(pid.value)
            if exe is None:
                exe = exe_cache.setdefault(pid.value, _exe_of_pid(pid.value))
            if exe in exe_names:
                buf = ctypes.create_unicode_buffer(length + 1)
                _user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value:
                    titles.append(buf.value)
            return True

        _user32.EnumWindows(_on_window, 0)
        return titles

else:
    def _enum_titles(exe_names: set[str]) -> list[str]:
        """POSIX: detecção por título de janela ainda não existe (#104) — lista vazia."""
        return []


def window_titles(exe_names: set[str], timeout_s: float = _ENUM_TIMEOUT_S) -> list[str]:
    """Títulos das janelas top-level visíveis dos executáveis dados — com prazo.

    Roda `_enum_titles` numa thread daemon e espera no máximo `timeout_s` (#114):
    estourou o prazo (EnumWindows pendurado por um processo travado), devolve []
    e abandona a thread — quem chama segue sem título, nunca congela. Falha da
    enumeração também vira [] (título é cosmético, jamais derruba a detecção).
    """
    with _stuck_lock:
        _stuck[:] = [t for t in _stuck if t.is_alive()]
        if _stuck:
            # enumeração anterior ainda pendurada: não adianta (e não é seguro)
            # empilhar outra — o window manager segue travado
            return []

    box: dict[str, list[str]] = {}

    def _work() -> None:
        try:
            box["titles"] = _enum_titles(exe_names)
        except Exception:
            log.debug("enumeração de janelas falhou", exc_info=True)

    t = threading.Thread(target=_work, daemon=True, name="wintitles")
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        with _stuck_lock:
            _stuck.append(t)
        log.warning("enumeração de janelas não respondeu em %.1fs — seguindo sem títulos (#114)",
                    timeout_s)
        return []
    return box.get("titles", [])
