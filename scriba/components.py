"""Download/instalação dos componentes pesados — compartilhado wizard/Configurações (#154).

O instalador é enxuto de propósito (#142): modelo Whisper, bibliotecas NVIDIA e
separação de vozes (torch + pyannote) são baixados sob demanda. A lógica morava
no wizard de 1º uso (`qt/setup_wizard.py`), que era o ÚNICO caminho — quem pulava
um download lá ficava sem retry dentro do app. Este módulo extrai o worker,
UI-free (progresso via callbacks), para o wizard E a seção "Baixar componentes"
da aba Sobre das Configurações usarem o mesmo código.

Contrato: `run(plan, progress, frac)` nunca levanta — falha vira (False, notas) e
o app segue funcionando sem o componente (re-tentável).
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from pathlib import Path

from . import sysprobe, updates

log = logging.getLogger("scriba.components")

# pesos (MB estimados) — a barra avança proporcional a eles; o modelo tem % real
# por bytes (crescimento do cache HF), os pips avançam ao concluir o item
CUDA_MB = 3000
VOICES_MB = 3000
CUDA_PKGS = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12"]
VOICES_PKGS = ["torch", "pyannote.audio>=4,<5"]


def plan_items(model: str | None = None, cuda: bool = False,
               voices: bool = False) -> list[tuple[str, str, int]]:
    """[(chave, rótulo de exibição, peso_mb)] do que será baixado. A chave do
    modelo carrega o nome ('model:small') — `run` não re-parseia rótulo."""
    items: list[tuple[str, str, int]] = []
    if model:
        model_mb = sysprobe.MODEL_DOWNLOAD_MB.get(model, 1500)
        items.append((f"model:{model}", f"modelo de transcrição {model} (~{model_mb} MB)", model_mb))
    if cuda:
        items.append(("cuda", "bibliotecas NVIDIA (cuBLAS/cuDNN, ~3 GB)", CUDA_MB))
    if voices:
        items.append(("voices", "separação de vozes (torch + pyannote, ~3 GB)", VOICES_MB))
    return items


def dir_mb(path) -> float:
    """Tamanho (MB) de uma árvore de diretório; 0 em qualquer erro. Base do % real
    do modelo: mede o CRESCIMENTO do cache HF durante o download — robusto a versão
    de lib (nada de depender de tqdm interno do huggingface_hub)."""
    try:
        return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file()) / 1048576
    except Exception:
        return 0.0


def model_cache_dir(model: str):
    """Pasta do cache HF onde o modelo vai cair (existente ou futura). NUNCA levanta:
    a pasta é só a régua do % real (dir_mb) — errar a régua não pode matar o download
    (sem huggingface_hub no ambiente, cai no caminho padrão do HF)."""
    try:
        from huggingface_hub.constants import HF_HUB_CACHE

        hub = Path(HF_HUB_CACHE)
    except Exception:
        hub = Path.home() / ".cache" / "huggingface" / "hub"
    try:
        from faster_whisper.utils import _MODELS

        repo = _MODELS.get(model, f"Systran/faster-whisper-{model}")
    except Exception:
        repo = f"Systran/faster-whisper-{model}"
    return hub / ("models--" + repo.replace("/", "--"))


def _download_model(model: str, progress) -> tuple[bool, str]:
    """Baixa/cacheia o modelo Whisper. Devolve (ok, nota-de-erro-ou-'')."""
    try:
        from faster_whisper import WhisperModel

        WhisperModel(model, device="cpu", compute_type="int8")  # baixa/cacheia
        progress(f"modelo {model} pronto.")
        return True, ""
    except Exception as e:
        progress(f"modelo falhou ({e}) - o app baixa na primeira transcrição.")
        return False, f"modelo: {e}"


def _install_extras(extras: list[str], progress) -> tuple[bool, str]:
    """Instala pacotes pip: congelada → addons in-process; git/venv → pip da venv."""
    progress("instalando componentes: " + ", ".join(extras) + "…")
    if updates.is_frozen_install():
        from . import addons

        return addons.install_to_addons(extras, progress=progress)
    r = subprocess.run(
        [updates._pip_interpreter(sys.executable), "-m", "pip", "install", *extras],
        capture_output=True, text=True, timeout=3600,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode == 0, (r.stderr or r.stdout or "")[-400:]


def run(plan: list[tuple[str, str, int]], progress, frac, poll: bool = True) -> tuple[bool, str]:
    """Executa o plano com % real na barra: `progress(str)` recebe as linhas de
    status, `frac(int 0..1000)` o avanço ponderado pelos MB de cada item. `poll`
    desligado roda sem a thread de medição do cache (testes/inline). Nunca levanta:
    devolve (ok_geral, notas-de-erro)."""
    ok_all, notes = True, []
    total_mb = sum(mb for _k, _l, mb in plan) or 1
    done_mb = 0.0

    def _p(line: str) -> None:
        # espelha no scriba.log: relatos de campo ("não baixou nada") chegavam sem
        # evidência nenhuma — a única saída era o label transiente do wizard
        log.info("componentes: %s", line)
        progress(line)

    log.info("componentes: plano = %s", [k for k, _l, _mb in plan] or "(vazio)")

    def _frac(item_done_mb: float) -> None:
        frac(min(1000, int(1000 * (done_mb + item_done_mb) / total_mb)))

    for key, _label, weight in plan:
        _frac(0)
        # resiliência POR ITEM: uma exceção num download não pode matar os demais
        # (a versão antiga do wizard abortava o plano inteiro — quem tropeçava no
        # modelo ficava também sem CUDA/vozes, sem nenhuma pista do porquê)
        try:
            if key.startswith("model:"):
                model = key.split(":", 1)[1]
                _p(f"baixando o modelo {model}…")
                cache = model_cache_dir(model)
                base = dir_mb(cache)
                stop = threading.Event()

                def _poll(cache=cache, base=base, weight=weight, stop=stop):
                    while not stop.wait(0.5):
                        _frac(min(weight, max(0.0, dir_mb(cache) - base)))

                if poll:
                    threading.Thread(target=_poll, daemon=True, name="dl-poll").start()
                try:
                    ok, note = _download_model(model, _p)
                finally:
                    stop.set()
            else:
                extras = CUDA_PKGS if key == "cuda" else VOICES_PKGS
                ok, note = _install_extras(extras, _p)
                if ok:
                    _p("componentes instalados.")
                    note = ""
                else:
                    _p("componentes falharam - dá para tentar de novo depois.")
                    note = f"componentes: {note}"
        except Exception as e:  # rede caiu no meio etc.: quem chama nunca trava
            log.exception("componente %s falhou", key)
            _p(f"{key} falhou ({e}) - os demais itens continuam.")
            ok, note = False, f"{key}: {e}"
        if not ok:
            ok_all = False
            if note:
                notes.append(note)
        done_mb += weight
        _frac(0)
    return ok_all, "; ".join(notes)
