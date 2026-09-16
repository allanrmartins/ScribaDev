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

# Regras compartilhadas com o spec do mac (installer/speclib.py): stdlib inteira
# COM submódulos, scipy inteiro quando presente e a asserção de build.
import sys as _sys

_sys.path.insert(0, str(REPO / "installer"))
import speclib  # noqa: E402

# stdlib INTEIRA no bundle (#187): a análise estática só embarca a stdlib que o
# código do repo importa - mas os addons (torch/pyannote, instalados sob demanda
# FORA da análise) importam stdlib em runtime, e módulo fora do bundle não
# existe num exe congelado. Caso vivido: pyannote importa `timeit`, o bundle não
# o tinha, e a diarização nunca rodou em NENHUMA instalação pelo instalador
# ("No module named 'timeit'", falha silenciosa com fallback p/ "Participantes").
# Desde a #196/#197 vai COM os submódulos de cada pacote: só o nome de topo
# deixava `unittest.mock` (que o próprio `import torch` puxa) de fora, e a
# diarização seguia morta em toda instalação por instalador.
_stdlib = speclib.stdlib_hiddenimports()

# Pacotes compartilhados com os addons (scipy, safetensors) INTEIROS quando
# estão na venv de build (#197): coletados parcialmente, o pacote do bundle
# vence o `__path__` e a cópia completa do addons nunca é consultada - "No
# module named 'scipy.cluster'" / 'safetensors.numpy' na diarização. Onde não
# estão instalados (runner do CI), nada entra e o addons cobre.
_scipy_datas, _scipy_binaries, _scipy_hidden = speclib.collect_all_compartilhados()

_common = dict(
    pathex=[str(REPO)],
    binaries=_pip_binaries + _scipy_binaries,
    datas=_pkg_datas + _pip_datas + _fw_datas + _scipy_datas,
    # typing_extensions NO bundle (#167): winrt/anyio importam-no e, fora do
    # bundle, a resolução cai no addons — se o pip estiver reescrevendo a pasta,
    # até os toasts do app morrem com EACCES. Bundlado, o FrozenImporter (meta
    # path) vence o addons sempre.
    hiddenimports=_pip_hidden + ["typing_extensions"] + _stdlib + _scipy_hidden,
    excludes=_excludes,
    noarchive=False,
    module_collection_mode=_pip_collection_mode,
)

a_cli = Analysis([str(Path(SPECPATH) / "entry_cli.py")], **_common)
a_tray = Analysis([str(Path(SPECPATH) / "entry_tray.py")], **_common)

# O build FALHA se o que os componentes baixados importam em runtime ficou de
# fora (#196/#197) - melhor um build vermelho que uma versão com a diarização
# morta em silêncio em toda instalação por instalador.
for _a in (a_cli, a_tray):
    speclib.exigir_tudo(_a)

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
