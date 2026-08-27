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

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

REPO = Path(SPECPATH).resolve().parents[1]

VERSION = re.search(r'__version__\s*=\s*"([^"]+)"',
                    (REPO / "scriba" / "__init__.py").read_text(encoding="utf-8")).group(1)

_excludes = [
    "torch", "torchaudio", "torchcodec", "pyannote", "pyannote.audio", "triton",
]

_pip_datas, _pip_binaries, _pip_hidden = collect_all("pip")

# pip como FONTE em disco, não no PYZ (#164, espelho do spec Windows): o distlib
# vendorizado enumera recursos via finder do loader e não conhece o do PyInstaller
# — in-process ele morria com "Unable to locate finder for 'pip._vendor.distlib'"
# ao instalar as wheels, e todo download de componentes falhava ("pip retornou 2").
_pip_collection_mode = {"pip": "py"}

# mlx (transcrição Metal, #104 M5) — o pacote precisa de TRÊS coletas, e faltando
# qualquer uma o `import mlx_whisper` morre no app instalado (ficou tudo em CPU):
#   1. collect_all: os submódulos PUROS que o core.so importa em tempo de execução
#      (mlx._reprlib_fix e cia.). O scanner estático não os vê — o import vem de
#      dentro do nanobind — e a falha aparece como o inútil "Encountered an error
#      while initializing the extension.";
#   2. o mlx.metallib (155 MB de shaders Metal compilados) vem nos datas do
#      collect_all — o mlx o procura AO LADO do libmlx.dylib (dladdr);
#   3. o libjaccl.dylib no destino "." (raiz do bundle = Contents/Frameworks):
#      é dependência @rpath do próprio libmlx, o scanner do PyInstaller a perde, e
#      os rpaths do libmlx procuram exatamente aí (`mlx/lib/../..`).
# Tudo vazio quando o mlx não está instalado (build fora do Apple Silicon).
_mlx_datas, _mlx_binaries, _mlx_hidden = collect_all("mlx")
_mlx_binaries += [(src, ".") for src, _dest in collect_dynamic_libs("mlx")]

# ...e o mlx_whisper tem assets/ próprios (mel_filters.npz + os .tiktoken). Sem eles
# o modelo até carrega "em metal" e morre na 1ª transcrição, com um erro que não
# menciona arquivo nenhum: "[load_npz] Input must be a zip file...".
_mlxw_datas, _mlxw_binaries, _mlxw_hidden = collect_all("mlx_whisper")

# faster-whisper: o silero_vad_v6.onnx (assets/) não é código e o PyInstaller não o
# leva sozinho — como o transcriber roda SEMPRE com vad_filter=True, sem ele a
# transcrição morre em "NO_SUCHFILE: Load model ... silero_vad_v6.onnx failed".
_fw_datas = collect_data_files("faster_whisper", includes=["**/*.onnx"])

# Dados do pacote: `assets` (ícones do app/bandeja) e `qt/icons` (SVGs Fluent da UI).
# Os SVGs faltavam até a 1.4.3 e o app instalado abria SEM ícone nenhum na UI (a
# engrenagem da config e cia.) — theme.icon() falha graciosamente e não avisa.
_pkg_datas = [
    (str(REPO / "scriba" / "assets"), "scriba/assets"),
    (str(REPO / "scriba" / "qt" / "icons"), "scriba/qt/icons"),
]

# stdlib INTEIRA no bundle (#187) — mesmo racional do spec Windows: os addons
# (torch/pyannote, fora da análise estática) importam stdlib em runtime, e
# módulo fora do bundle não existe num exe congelado (caso vivido: `timeit`,
# diarização morta em silêncio em toda instalação por instalador).
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
    binaries=_pip_binaries + _mlx_binaries + _mlxw_binaries,
    datas=_pkg_datas + _pip_datas + _mlx_datas + _mlxw_datas + _fw_datas,
    # typing_extensions no bundle (#167) — mesmo racional do spec Windows: core
    # do app não pode depender do addons em estado transitório de instalação
    hiddenimports=_pip_hidden + _mlx_hidden + _mlxw_hidden + ["typing_extensions"] + _stdlib,
    excludes=_excludes,
    noarchive=False,
    module_collection_mode=_pip_collection_mode,
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
        # OBRIGATÓRIO: o BUNDLE do PyInstaller herda o `console` do ÚLTIMO EXE do
        # COLLECT (aqui a CLI, console=True) e, com isso, grava LSBackgroundOnly=True
        # no Info.plist. Um app background-only NÃO pode ser ativado pelo macOS: as
        # janelas aparecem, mas o foco/menu bar fica no app anterior e TODO o teclado
        # vai para lá (bug do DMG da 1.4.3). Explicitar False aqui vence o default do
        # PyInstaller (o info_plist do .spec é mesclado por cima) e não depende da
        # ordem dos EXEs no COLLECT.
        "LSBackgroundOnly": False,
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
