"""Títulos das janelas visíveis de um conjunto de processos (Win32/ctypes).

Usado pela detecção de calls em navegador: o registro do mic só diz que
"chrome.exe está com o microfone aberto" — é o título de alguma janela do
navegador (a aba ativa) que diz SE aquilo é uma reunião (Meet, Teams web...).
"""

from __future__ import annotations

import sys

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

    def window_titles(exe_names: set[str]) -> list[str]:
        """Títulos das janelas top-level visíveis pertencentes aos executáveis dados.

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
    def window_titles(exe_names: set[str]) -> list[str]:
        """POSIX: detecção por título de janela ainda não existe (#104) — lista vazia."""
        return []
