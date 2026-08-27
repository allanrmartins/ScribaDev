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

# ...mas como FONTE em disco, não no PYZ (#164): o distlib vendorizado do pip
# enumera os próprios recursos via finder do loader (distlib/resources.py) e só
# conhece FileFinder/zipimport — sob o PyiFrozenImporter ele morre com "Unable
# to locate finder for 'pip._vendor.distlib'" na fase de instalar as wheels
# (depois de baixar os GB todos), e TODO download de componentes falhava com
# "pip retornou 2". Com 'py' o pip inteiro vira .py real em _internal/ e importa
# pelo FileFinder normal, como um pip de verdade.
_pip_collection_mode = {"pip": "py"}

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

# stdlib INTEIRA no bundle (#187): a análise estática só embarca a stdlib que o
# código do repo importa - mas os addons (torch/pyannote, instalados sob demanda
# FORA da análise) importam stdlib em runtime, e módulo fora do bundle não
# existe num exe congelado. Caso vivido: pyannote importa `timeit`, o bundle não
# o tinha, e a diarização nunca rodou em NENHUMA instalação pelo instalador
# ("No module named 'timeit'", falha silenciosa com fallback p/ "Participantes").
# Mesmo padrão do typing_extensions/#167, generalizado: custa poucos MB e fecha
# a classe inteira do problema. Fora ficam só os pacotes-mamute sem uso
# plausível por uma lib de ML; nome ausente na plataforma vira warning inócuo.
import importlib.util as _ilu
import sys as _sys

_STDLIB_DENY = {
    "antigravity", "this", "idlelib", "lib2to3", "turtledemo", "turtle",
    "tkinter", "test", "ensurepip",
}
def _importavel(m):
    try:
        return _ilu.find_spec(m) is not None
    except Exception:
        return False  # módulo deprecated/quebrado: fora do bundle, sem drama


_stdlib = sorted(
    m for m in _sys.stdlib_module_names
    if not m.startswith("_") and m not in _STDLIB_DENY and _importavel(m)
)

_common = dict(
    pathex=[str(REPO)],
    binaries=_pip_binaries,
    datas=_pkg_datas + _pip_datas + _fw_datas,
    # typing_extensions NO bundle (#167): winrt/anyio importam-no e, fora do
    # bundle, a resolução cai no addons — se o pip estiver reescrevendo a pasta,
    # até os toasts do app morrem com EACCES. Bundlado, o FrozenImporter (meta
    # path) vence o addons sempre.
    hiddenimports=_pip_hidden + ["typing_extensions"] + _stdlib,
    excludes=_excludes,
    noarchive=False,
    module_collection_mode=_pip_collection_mode,
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
