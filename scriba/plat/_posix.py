"""Backend POSIX (Linux + macOS) da camada de plataforma (#104).

Quando Linux e macOS divergirem de verdade (captura, notificações nativas),
este módulo se divide em _linux.py/_mac.py; por ora os ramos por SO cabem aqui.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_data_dir() -> Path:
    """Diretório de dados do app na convenção de cada SO.

    Linux: $XDG_DATA_HOME/ScribaDev (fallback ~/.local/share/ScribaDev).
    macOS: ~/Library/Application Support/ScribaDev.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ScribaDev"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "ScribaDev"


def default_recordings_dir() -> Path:
    """Fora do Windows não existe C:\\temp — as gravações ficam sob o app_data_dir."""
    return app_data_dir() / "gravacoes"


def open_path(path) -> None:
    """Abre arquivo ou pasta no gerenciador do SO (xdg-open no Linux, open no macOS)."""
    import subprocess

    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(path)])
