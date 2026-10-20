"""Cria o "ScribaDev Dev.app": lançador do modo CÓDIGO-FONTE no macOS (setup.sh).

O app roda do venv editável (fechar e abrir já pega o código novo do repo, como no
Windows), mas precisa nascer dentro de um .app: é o bundle que carrega as frases de
uso do Info.plist, e rodando cru do venv o macOS nega o áudio do sistema EM
SILÊNCIO (a call grava só a sua voz). O executável do bundle é um script que só
faz `exec` do Python do venv - o processo segue atribuído ao bundle.

Uso: python3 installer/macos/make_source_app.py <venv> [destino.app]
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from bundle_info import PERMISSION_PLIST  # noqa: E402

APP_NAME = "ScribaDev Dev.app"
BUNDLE_ID = "dev.scribadev.source"   # != do DMG (dev.scribadev): permissões separadas

_LAUNCHER = """#!/bin/bash
# Lançador do ScribaDev em modo código-fonte (gerado por installer/macos/make_source_app.py).
# SCRIBADEV_APP_BUNDLE: o autostart abre ESTE .app (via `open -a`), não o Python cru.
export SCRIBADEV_APP_BUNDLE="$(cd "$(dirname "$0")/../.." && pwd)"
exec {python} -c 'import sys; from scriba.cli import main_tray; sys.exit(main_tray())' "$@"
"""


def default_destination() -> Path:
    apps = Path("/Applications")
    if os.access(apps, os.W_OK):
        return apps / APP_NAME
    return Path.home() / "Applications" / APP_NAME


def _icns(out: Path) -> None:
    """scriba.png -> .icns com as ferramentas do sistema (mesma receita do build.sh)."""
    png = REPO / "scriba" / "assets" / "scriba.png"
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "scriba.iconset"
        iconset.mkdir()
        for s in (16, 32, 128, 256, 512):
            for name, px in ((f"icon_{s}x{s}.png", s), (f"icon_{s}x{s}@2x.png", s * 2)):
                subprocess.run(["sips", "-z", str(px), str(px), str(png), "--out", str(iconset / name)],
                               check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)


def build(venv: Path, dest: Path) -> Path:
    python = venv / "bin" / "python3"
    if not python.exists():
        raise SystemExit(f"venv sem python: {python}")
    if dest.exists():
        shutil.rmtree(dest)
    macos = dest / "Contents" / "MacOS"
    res = dest / "Contents" / "Resources"
    macos.mkdir(parents=True)
    res.mkdir()
    exe = macos / "ScribaDev"
    exe.write_text(_LAUNCHER.format(python=_sh_quote(str(python))), encoding="utf-8")
    exe.chmod(0o755)
    _icns(res / "scriba.icns")
    info = {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": "ScribaDev",
        "CFBundleDisplayName": "ScribaDev",
        "CFBundleExecutable": "ScribaDev",
        "CFBundleIconFile": "scriba.icns",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "source",
        "CFBundleVersion": "1",
        "LSBackgroundOnly": False,
        "NSHighResolutionCapable": True,
        **PERMISSION_PLIST,
    }
    with open(dest / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump(info, f)
    # assinatura ad-hoc: o macOS guarda as permissões concedidas por identidade de código
    subprocess.run(["codesign", "-s", "-", "--force", str(dest)], check=True, capture_output=True)
    return dest


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    dest = Path(argv[2]) if len(argv) > 2 else default_destination()
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"lançador: {build(Path(argv[1]), dest)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
