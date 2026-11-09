"""Prints feitos pelo atalho do SISTEMA durante a call (#229).

O atalho do sistema nunca é sequestrado: o print continua indo para onde iria e o
ScribaDev só guarda uma cópia na pasta da gravação.

- macOS: Cmd+Shift+3/4/5 salvam um arquivo na pasta de capturas (padrão: Mesa,
  configurável em `com.apple.screencapture location`). `FolderWatch` observa essa
  pasta enquanto a gravação dura e copia cada print novo.
- Windows: a tecla Print Screen é observada por `printkey_win` (gancho de teclado
  que só escuta), e o app faz a própria captura na hora.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import prints

log = logging.getLogger("scriba.printwatch")

_POLL_S = 1.5


def mac_screenshot_dir() -> Path:
    """Pasta onde o macOS salva os prints (Ajustes do app Captura de Tela)."""
    try:
        out = subprocess.run(["defaults", "read", "com.apple.screencapture", "location"],
                             capture_output=True, text=True, timeout=5)
        loc = (out.stdout or "").strip()
        if out.returncode == 0 and loc:
            p = Path(loc).expanduser()
            if p.is_dir():
                return p
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / "Desktop"


def _scan(folder: Path) -> list[tuple[str, float, int]]:
    out = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    if e.is_file():
                        st = e.stat()
                        out.append((e.name, st.st_mtime, st.st_size))
                except OSError:
                    continue
    except OSError:
        pass
    return out


class FolderWatch:
    """Observa a pasta de capturas do sistema durante UMA gravação.

    `on_shot(src, when)` recebe cada print novo (já completo no disco) com o
    horário em que foi feito. Roda numa thread própria; `stop()` encerra.
    """

    def __init__(self, folder: Path, since: float, on_shot: Callable[[Path, datetime], None]):
        self.folder = Path(folder)
        self.since = since
        self.on_shot = on_shot
        self._seen: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="printwatch")

    def start(self) -> "FolderWatch":
        # arquivos que já estavam lá no início nunca contam, mesmo com mtime novo
        for name, _mtime, _size in _scan(self.folder):
            self._seen[name] = -1
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def poll(self) -> None:
        """Uma ronda (pública para os testes)."""
        entries = _scan(self.folder)
        mtimes = {n: m for n, m, _s in entries}
        for name in prints.new_system_shots(entries, self.since, self._seen):
            try:
                self.on_shot(self.folder / name, datetime.fromtimestamp(mtimes[name]))
            except Exception:
                log.exception("print do sistema: falha ao guardar %s", name)

    def _run(self) -> None:
        while not self._stop.wait(_POLL_S):
            try:
                self.poll()
            except Exception:
                log.exception("observador de prints falhou (ignorado)")


def copy_into(src: Path, folder: Path, when: datetime) -> Path:
    """Copia um print do sistema para <pasta da gravação>/prints/ (nome pelo horário)."""
    dst = prints.unique_path(folder, when, src.suffix or ".png")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def start_for(recording, on_saved: Callable[[], None] | None = None) -> FolderWatch | None:
    """Liga o observador da pasta de capturas para a gravação (só macOS)."""
    if sys.platform != "darwin":
        return None

    def on_shot(src: Path, when: datetime) -> None:
        dst = copy_into(src, recording.folder, when)
        # posição na gravação: agora menos a idade do arquivo
        offset = recording.duration_seconds() - max(0.0, time.time() - when.timestamp())
        if recording.note_screenshot(dst, when, "tecla", offset):
            log.info("print do sistema guardado: %s", dst.name)
            if on_saved:
                on_saved()
        else:
            dst.unlink(missing_ok=True)

    since = time.time()
    return FolderWatch(mac_screenshot_dir(), since, on_shot).start()
