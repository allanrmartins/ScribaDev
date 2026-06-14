"""Início automático com o Windows: atalho na pasta Startup do usuário (sem admin)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_LNK_NAME = "ScribaDev.lnk"


def _startup_dir() -> Path:
    return Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Startup"


def is_enabled() -> bool:
    return (_startup_dir() / _LNK_NAME).exists()


def set_autostart(enable: bool) -> int:
    lnk = _startup_dir() / _LNK_NAME
    if not enable:
        lnk.unlink(missing_ok=True)
        print("autostart desligado")
        return 0

    target = Path(sys.prefix) / "Scripts" / "scribadev-tray.exe"
    if not target.exists():
        print(f"não encontrei {target} — rode o setup.ps1 de novo")
        return 1
    from . import util

    icon_line = f"$s.IconLocation = '{util.ICON_ICO}'; " if util.ICON_ICO.exists() else ""
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{lnk}'); "
        f"$s.TargetPath = '{target}'; "
        f"$s.WorkingDirectory = '{target.parent}'; "
        f"{icon_line}"
        "$s.Description = 'ScribaDev - gravacao automatica de calls do Teams'; "
        "$s.Save()"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(f"falha ao criar atalho: {proc.stderr.strip()}")
        return 1
    print(f"autostart ligado: {lnk}")
    return 0
