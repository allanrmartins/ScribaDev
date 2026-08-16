# Spec do PyInstaller p/ o ScribaDev.app (macOS arm64, #143 do épico #138).
#
# Espelho do installer/windows/scribadev.spec: dois executáveis num COLLECT
# (`scribadev` CLI + `ScribaDevApp` windowed) e, no mac, o BUNDLE embrulha tudo
# num ScribaDev.app com o Info.plist certo. As usage strings de permissão são
# CRÍTICAS (docs/port-mac.md): sem NSAudioCaptureUsageDescription a captura do
# áudio do sistema falha EM SILÊNCIO; o prompt do microfone precisa da dele.
#
# Build CPU/Metal enxuto: torch/pyannote ficam FORA (wizard baixa sob demanda em
# APP_DIR/addons — o pip vai junto no bundle p/ isso, como no Windows). mlx-whisper
# entra pelo pyproject (marker darwin+arm64) — transcrição Metal sem download extra.
#
# Rode via installer/macos/build.sh (gera o .icns e o DMG).

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

REPO = Path(SPECPATH).resolve().parents[1]

VERSION = re.search(r'__version__\s*=\s*"([^"]+)"',
                    (REPO / "scriba" / "__init__.py").read_text(encoding="utf-8")).group(1)

_excludes = [
    "torch", "torchaudio", "torchcodec", "pyannote", "pyannote.audio", "triton",
]

_pip_datas, _pip_binaries, _pip_hidden = collect_all("pip")

_common = dict(
    pathex=[str(REPO)],
    binaries=_pip_binaries,
    datas=[(str(REPO / "scriba" / "assets"), "scriba/assets")] + _pip_datas,
    hiddenimports=_pip_hidden,
    excludes=_excludes,
    noarchive=False,
)

a_cli = Analysis([str(Path(SPECPATH) / "entry_cli.py")], **_common)
a_app = Analysis([str(Path(SPECPATH) / "entry_tray.py")], **_common)

pyz_cli = PYZ(a_cli.pure)
pyz_app = PYZ(a_app.pure)

exe_cli = EXE(
    pyz_cli, a_cli.scripts, [],
    exclude_binaries=True,
    name="scribadev",
    console=True,
)
exe_app = EXE(
    pyz_app, a_app.scripts, [],
    exclude_binaries=True,
    name="ScribaDevApp",
    console=False,
)

coll = COLLECT(
    exe_app, a_app.binaries, a_app.datas,
    exe_cli, a_cli.binaries, a_cli.datas,
    name="scribadev",
)

_icns = Path(SPECPATH) / "build" / "scriba.icns"  # gerado pelo build.sh (sips/iconutil)

app = BUNDLE(
    coll,
    name="ScribaDev.app",
    icon=str(_icns) if _icns.exists() else None,
    bundle_identifier="dev.scribadev",
    version=VERSION,
    info_plist={
        "CFBundleName": "ScribaDev",
        "CFBundleDisplayName": "ScribaDev",
        "CFBundleShortVersionString": VERSION,
        "NSHighResolutionCapable": True,
        # CATapDescription (process tap do áudio do sistema) é macOS 14.2+
        "LSMinimumSystemVersion": "14.2",
        "NSMicrophoneUsageDescription":
            "O ScribaDev grava o seu microfone durante as reuniões para "
            "transcrever o que você disse. O áudio nunca sai do seu Mac.",
        "NSAudioCaptureUsageDescription":
            "O ScribaDev captura o áudio das reuniões (Teams, Zoom, navegador) "
            "para transcrever o que os participantes disseram. Nada sai do seu Mac.",
    },
)
