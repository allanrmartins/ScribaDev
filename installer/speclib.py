"""Lógica compartilhada pelos .spec do PyInstaller (Windows e macOS).

Os dois specs repetiam o mesmo bloco de stdlib; agora ele mora aqui, junto das
regras que nasceram das issues #196/#197 (componentes baixados sob demanda que
importam, em runtime, coisas que a análise estática do PyInstaller não enxerga).
Importado pelos specs via `sys.path` (a pasta installer/ não é pacote) e pelos
testes (tests/test_installers.py) - por isso o PyInstaller só é importado DENTRO
das funções que precisam dele: a suíte roda sem ele instalado.
"""

from __future__ import annotations

import importlib.util
import sys

# Pacotes-mamute da stdlib sem uso plausível por uma lib de ML - o resto vai
# inteiro (#187). Nome ausente na plataforma vira warning inócuo do PyInstaller.
STDLIB_DENY = frozenset({
    "antigravity", "this", "idlelib", "lib2to3", "turtledemo", "turtle",
    "tkinter", "test", "ensurepip",
})

# Módulos que os ADDONS (torch/pyannote, instalados pelo wizard FORA da análise)
# importam em runtime e que a análise estática NÃO leva sozinha. O build falha
# na hora se algum ficar de fora (`exigir_no_bundle`), em vez de a diarização
# morrer em silêncio na máquina do usuário (#196/#197):
# - unittest.mock: o próprio `import torch` (torch/utils/_config_module.py) e o
#   lightning_utilities importam-no. `unittest/__init__` não importa `.mock`, então
#   o hiddenimport "unittest" de topo levava o pacote SEM ele. No mac o sintoma
#   era "No module named 'unittest.mock'" (#197); no Windows, o 1º `import torch`
#   falhava mudo no close() do Whisper e o 2º explodia com o falso "partially
#   initialized module 'torch' has no attribute 'autograd'" (#196).
ADDON_RUNTIME_MODULES = ("unittest.mock", "unittest.util")

# Pacotes de terceiros que o app importa SÓ EM PARTE e que torch/pyannote (nos
# addons) precisam INTEIROS. O PyInstaller leva apenas o que a análise alcança,
# e como o pacote do bundle vence o `__path__`, a cópia completa do addons nunca
# é consultada - a diarização morria em "No module named 'scipy.cluster'" (#197)
# e, na bancada, em 'safetensors.numpy'. Regra: se entra, entra inteiro (ver
# `collect_all_compartilhados`). O valor é o que a asserção de build exige quando
# o pacote está presente (módulos que a coleta parcial comprovadamente perdia):
# - scipy: pyannote.audio.pipelines.clustering importa scipy.cluster.hierarchy,
#   que puxa scipy._lib._disjoint_set (mlx_whisper e pandas importam só parte);
# - safetensors: pyannote.audio.core.calibration importa safetensors.numpy (o
#   huggingface_hub só importa safetensors.torch).
# Medido em installer E2E (transcribe no exe congelado com addons = venv):
# fora estes, nada mais que o pipeline carrega falta no bundle.
COMPARTILHADOS_COM_ADDONS = {
    "scipy": ("scipy.cluster.hierarchy", "scipy._lib._disjoint_set"),
    "safetensors": ("safetensors.numpy",),
}


def importavel(nome: str) -> bool:
    """find_spec sem drama: módulo deprecated/quebrado conta como ausente."""
    try:
        return importlib.util.find_spec(nome) is not None
    except Exception:
        return False


def e_pacote(nome: str) -> bool:
    try:
        spec = importlib.util.find_spec(nome)
    except Exception:
        return False
    return bool(spec is not None and spec.submodule_search_locations)


def stdlib_nomes() -> list[str]:
    """Nomes de topo da stdlib que vão para o bundle (menos o DENY e os privados)."""
    return sorted(
        m for m in sys.stdlib_module_names
        if not m.startswith("_") and m not in STDLIB_DENY and importavel(m)
    )


def stdlib_hiddenimports() -> list[str]:
    """stdlib INTEIRA, COM os submódulos de cada pacote (#187 + #196/#197).

    Até a 1.4.19 a lista tinha só o nome de topo: a análise do PyInstaller segue
    apenas o que cada `__init__` importa, e `unittest.mock`, `email.mime.*`,
    `xml.dom.*` e cia. ficavam de fora. Um pacote é expandido com
    collect_submodules (um subprocesso isolado por pacote, ~1 s cada); módulo
    solto entra pelo nome. Pacote que não importa na plataforma (curses no
    Windows) vira warning do PyInstaller e segue.
    """
    from PyInstaller.utils.hooks import collect_submodules

    out: list[str] = []
    for nome in stdlib_nomes():
        if e_pacote(nome):
            out.extend(collect_submodules(nome) or [nome])
        else:
            out.append(nome)
    return out


def collect_all_se_instalado(pacote: str, *, sem_tests: bool = True):
    """collect_all(pacote) quando ele está na venv de build; senão (datas,
    binaries, hiddenimports) vazios.

    Caso do scipy (#197): o mlx_whisper (mac) e o pandas (venv de dev) importam
    PARTE dele, o PyInstaller leva só essa parte, e como o pacote do bundle vence
    o `__path__`, o scipy COMPLETO que o wizard instala em addons/ nunca é
    consultado - a diarização morria em "No module named 'scipy.cluster'". Se o
    scipy entra, entra inteiro. Onde ele não está instalado (runner Windows do CI,
    que só faz `pip install -e .`), nada é adicionado e o addons cobre tudo.
    """
    if not importavel(pacote):
        return [], [], []
    from PyInstaller.utils.hooks import collect_all

    filtro = (lambda nome: ".tests" not in nome) if sem_tests else None
    return collect_all(pacote, filter_submodules=filtro)


def collect_all_compartilhados():
    """(datas, binaries, hiddenimports) de TODOS os pacotes compartilhados com os
    addons que estiverem na venv de build (`COMPARTILHADOS_COM_ADDONS`)."""
    datas: list = []
    binaries: list = []
    hidden: list = []
    for pacote in COMPARTILHADOS_COM_ADDONS:
        d, b, h = collect_all_se_instalado(pacote)
        datas += d
        binaries += b
        hidden += h
    return datas, binaries, hidden


def modulos_do_bundle(analysis) -> set[str]:
    """Nomes dos módulos puros que a Analysis vai colocar no PYZ."""
    return {entrada[0] for entrada in analysis.pure}


def exigir_no_bundle(analysis, modulos, *, quando_presente: str | None = None) -> None:
    """Falha o build se algum dos `modulos` não entrou na Analysis.

    `quando_presente`: só exige quando esse prefixo de pacote está no bundle (o
    scipy é opcional: sem ele no bundle, o addons resolve; COM ele, tem de vir
    inteiro). Levantar aqui custa um build; deixar passar custou uma versão
    inteira com a diarização morta em toda instalação por instalador (#187,
    #196, #197).
    """
    presentes = modulos_do_bundle(analysis)
    if quando_presente and not any(
        m == quando_presente or m.startswith(quando_presente + ".") for m in presentes
    ):
        return
    faltam = [m for m in modulos if m not in presentes]
    if faltam:
        raise SystemExit(
            "módulos que os componentes baixados (torch/pyannote) importam em runtime "
            f"ficaram FORA do bundle: {', '.join(faltam)} - veja installer/speclib.py"
        )


def exigir_tudo(analysis) -> None:
    """A asserção de build completa: stdlib que os addons usam + cada pacote
    compartilhado que entrou no bundle veio inteiro."""
    exigir_no_bundle(analysis, ADDON_RUNTIME_MODULES)
    for pacote, modulos in COMPARTILHADOS_COM_ADDONS.items():
        exigir_no_bundle(analysis, modulos, quando_presente=pacote)
