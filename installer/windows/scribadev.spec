# Spec do PyInstaller p/ o instalador Windows (#141/#142 do épico #138).
#
# UM dist com DOIS executáveis compartilhando o mesmo _internal:
#   - scribadev.exe      (console)  — a CLI de sempre (run/doctor/search/...)
#   - ScribaDevApp.exe   (windowed) — entry da bandeja (scriba.cli:main_tray),
#     sem janela de console; é o alvo dos atalhos/autostart do instalador.
#
# Build CPU-FIRST ENXUTO (decisão do épico): torch/pyannote/CUDA ficam FORA —
# a diarização é baixada sob demanda pelo wizard (Expressa/Avançada). Validado
# na PoC (issue #141): 459 MB, transcrição CPU real OK (PyAV decodifica áudio
# sem torch; o hook padrão do PyAV embarca as DLLs do ffmpeg).
#
# Rode via installer/windows/build.ps1 (usa a venv do app, que tem as deps).

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

REPO = Path(SPECPATH).resolve().parents[1]  # installer/windows -> raiz do repo

_excludes = [
    # deps pesadas baixadas sob demanda (wizard do 1º uso), nunca no bundle
    "torch", "torchaudio", "torchcodec", "pyannote", "pyannote.audio", "triton",
]

# pip vai NO bundle (#147): o wizard instala os addons (torch/pyannote/nvidia)
# em APP_DIR/addons com o pip in-process (scriba.addons.install_to_addons) —
# um exe congelado não tem `python -m pip`.
_pip_datas, _pip_binaries, _pip_hidden = collect_all("pip")

# Dados do pacote: `assets` (ícones do app/bandeja) e `qt/icons` (SVGs Fluent da UI).
# Os SVGs faltavam até a 1.4.3 e o app instalado abria SEM ícone nenhum na UI (a
# engrenagem da config e cia.) — theme.icon() falha graciosamente e não avisa.
# faster-whisper: o silero_vad_v6.onnx (assets/) não é código e o PyInstaller não o
# leva sozinho — como o transcriber roda SEMPRE com vad_filter=True, sem ele a
# transcrição morre em "NO_SUCHFILE: Load model ... silero_vad_v6.onnx failed".
_fw_datas = collect_data_files("faster_whisper", includes=["**/*.onnx"])

_pkg_datas = [
    (str(REPO / "scriba" / "assets"), "scriba/assets"),
    (str(REPO / "scriba" / "qt" / "icons"), "scriba/qt/icons"),
]

_common = dict(
    pathex=[str(REPO)],
    binaries=_pip_binaries,
    datas=_pkg_datas + _pip_datas + _fw_datas,
    hiddenimports=_pip_hidden,
    excludes=_excludes,
    noarchive=False,
)

a_cli = Analysis([str(Path(SPECPATH) / "entry_cli.py")], **_common)
a_tray = Analysis([str(Path(SPECPATH) / "entry_tray.py")], **_common)

pyz_cli = PYZ(a_cli.pure)
pyz_tray = PYZ(a_tray.pure)

ICON = str(REPO / "scriba" / "assets" / "scriba.ico")

exe_cli = EXE(
    pyz_cli, a_cli.scripts, [],
    exclude_binaries=True,
    name="scribadev",
    console=True,
    icon=ICON,
)
exe_tray = EXE(
    pyz_tray, a_tray.scripts, [],
    exclude_binaries=True,
    name="ScribaDevApp",
    console=False,   # windowed: bandeja/GUI sem console (equivale ao pythonw)
    icon=ICON,
)

coll = COLLECT(
    exe_cli, a_cli.binaries, a_cli.datas,
    exe_tray, a_tray.binaries, a_tray.datas,
    name="scribadev",
)
