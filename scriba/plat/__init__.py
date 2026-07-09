"""Camada de plataforma: despacho por SO (épico #104).

Regra de ouro: no Windows o backend `_win` executa exatamente o código que o app
sempre teve (movido, não reescrito) — só o ramo POSIX (`_posix`, Linux + macOS) é
novo. Módulos de negócio não tocam winreg/ctypes.windll/explorer.exe diretamente:
o que é específico de SO passa por aqui.

Este pacote não importa outros módulos do scriba (util importa plat; o contrário
criaria ciclo de import).
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    from . import _win as _backend
else:
    from . import _posix as _backend

app_data_dir = _backend.app_data_dir
default_recordings_dir = _backend.default_recordings_dir
has_nvidia_gpu = _backend.has_nvidia_gpu
open_path = _backend.open_path
