"""Backend Windows da camada de plataforma — comportamento idêntico ao histórico.

Código MOVIDO de util.py/config.py (não reescrito): qualquer mudança aqui é
mudança de comportamento no Windows e precisa de justificativa própria (#104).
"""

from __future__ import annotations

import os
from pathlib import Path


def app_data_dir() -> Path:
    """%LOCALAPPDATA%\\ScribaDev (fallback: home) — o APP_DIR de sempre."""
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ScribaDev"


def default_recordings_dir() -> Path:
    """Default histórico das gravações (config [output] recordings_dir vazio)."""
    return Path(r"C:\temp\scribadev\gravacoes")


def open_path(path) -> None:
    """Abre arquivo ou pasta no Explorer (substituto do os.startfile).

    O winrt (toasts) inicializa o COM do processo em MTA, e o ShellExecute do
    os.startfile falha em abrir pastas nesse modo ("o local não está
    disponível"). O explorer.exe em processo próprio não tem esse problema.
    """
    import subprocess

    subprocess.Popen(["explorer.exe", str(path)])
