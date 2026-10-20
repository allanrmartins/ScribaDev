"""Início automático com o sistema: atalho na pasta Startup (Windows) ou
LaunchAgent em ~/Library/LaunchAgents (macOS, #104 M6) — sem admin nos dois."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_LNK_NAME = "ScribaDev.lnk"
_MAC_LABEL = "dev.scribadev.tray"


def label() -> str:
    """Texto do item de menu/checkbox por SO ("Iniciar com o Windows"/"...o sistema")."""
    return "Iniciar com o Windows" if sys.platform == "win32" else "Iniciar com o sistema"


def _startup_dir() -> Path:
    return Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Startup"


def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_MAC_LABEL}.plist"


def _mac_app_bundle() -> Path | None:
    """O .app que abriu este processo: o do DMG (congelado) ou o lançador do modo
    código-fonte, que exporta SCRIBADEV_APP_BUNDLE (installer/macos/source_launcher.sh).

    O autostart precisa abrir o .APP, não o executável cru: é o bundle que carrega
    as frases de uso do microfone/áudio do sistema no Info.plist, e o macOS nega o
    áudio do sistema EM SILÊNCIO a quem não as tem - a call gravaria só a sua voz."""
    env = os.environ.get("SCRIBADEV_APP_BUNDLE")
    if env and Path(env).is_dir():
        return Path(env)
    if getattr(sys, "frozen", False):
        for parent in Path(sys.executable).resolve().parents:
            if parent.suffix == ".app":
                return parent
    return None


def is_enabled() -> bool:
    if sys.platform == "darwin":
        return _mac_plist_path().exists()
    if sys.platform != "win32":
        return False
    return (_startup_dir() / _LNK_NAME).exists()


def _mac_set_autostart(enable: bool) -> int:
    """LaunchAgent com RunAtLoad — vale a partir do próximo login, MESMA semântica
    do .lnk na Startup do Windows. Sem `launchctl` de propósito: carregar na hora
    subiria uma 2ª instância agora, e menos superfície de erro."""
    plist = _mac_plist_path()
    if not enable:
        plist.unlink(missing_ok=True)
        print("autostart desligado")
        return 0
    bundle = _mac_app_bundle()
    if bundle is not None:
        # via LaunchServices: o processo nasce "dentro" do .app (permissões certas)
        args = ["/usr/bin/open", "-a", str(bundle), "--args", "--minimized"]
    else:
        target = Path(sys.prefix) / "bin" / "scribadev-tray"
        if not target.exists():
            print(f"não encontrei {target} — rode o setup.sh de novo")
            return 1
        args = [str(target), "--minimized"]
    import plistlib

    data = {
        "Label": _MAC_LABEL,
        "ProgramArguments": args,  # autostart inicia só na bandeja (--minimized)
        "RunAtLoad": True,
    }
    plist.parent.mkdir(parents=True, exist_ok=True)
    with open(plist, "wb") as f:
        plistlib.dump(data, f)
    print(f"autostart ligado: {plist} (vale a partir do próximo login)")
    return 0


def set_autostart(enable: bool) -> int:
    if sys.platform == "darwin":
        return _mac_set_autostart(enable)
    if sys.platform != "win32":
        # .desktop do Linux vem em marco futuro (#104)
        print("autostart não suportado neste SO ainda")
        return 1
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
        "$s.Arguments = '--minimized'; "  # autostart inicia só na bandeja (o atalho normal abre a janela)
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
