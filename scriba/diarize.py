"""Diarização local (pyannote.audio): separa os participantes remotos por voz.

Tudo roda na máquina (GPU se houver). O modelo oficial é "gated" no Hugging Face:
o usuário precisa de um token de leitura gratuito e de aceitar os termos na
página do modelo — depois disso, o download acontece uma vez e o resto é offline.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import idlewatch
from .config import Diarization
from .transcriber import Segment

log = logging.getLogger("scriba.diarize")

Turn = tuple[float, float, str]  # (início, fim, rótulo da voz)

# Pacotes dos componentes de voz: só a AUSÊNCIA de um destes é "falta o extra".
_DEPS_TOPO = ("torch", "pyannote")


def purge_stale_submodules(pkg: str) -> list[str]:
    """Tira de sys.modules os submódulos ÓRFÃOS de `pkg` (topo não carregado).

    Quando um `import torch` falha no meio (#196: `unittest.mock` fora do bundle
    congelado), o Python remove `torch` de sys.modules mas DEIXA os submódulos que
    já tinham importado (`torch.autograd`, ...). Na tentativa seguinte, o novo
    `torch/__init__` encontra `torch.autograd` já em sys.modules e nunca o
    amarra como atributo do pacote novo - e o import explode num falso
    "partially initialized module 'torch' has no attribute 'autograd' (most
    likely due to a circular import)", escondendo a causa real. Limpar os órfãos
    faz a nova tentativa ser um import limpo, que falha (ou funciona) pelo motivo
    verdadeiro. Devolve o que removeu (p/ o log). Sem efeito se o topo está vivo.
    """
    import sys

    if pkg in sys.modules:
        return []
    orfaos = sorted(m for m in sys.modules if m.startswith(pkg + "."))
    for m in orfaos:
        del sys.modules[m]
    if orfaos:
        log.warning("import anterior de %s falhou no meio: limpando %d submódulo(s) órfão(s) "
                    "antes de tentar de novo (ex.: %s)", pkg, len(orfaos), orfaos[0])
    return orfaos


def deps_error_message(e: BaseException) -> str:
    """Mensagem certa p/ uma falha ao importar torch/pyannote (#196/#197).

    Só "falta o extra [diarization]" quando o que não existe é o PRÓPRIO torch ou
    pyannote. Qualquer outra falha (um módulo interno ausente no bundle, uma DLL
    que não carrega, o falso circular import da #196) é instalação/empacotamento
    - e mandar o usuário rodar `pip install` não resolve nada; a #197 levou uma
    tarde de diagnóstico por causa dessa mensagem.
    """
    faltando = getattr(e, "name", None) if isinstance(e, ModuleNotFoundError) else None
    if faltando and faltando.split(".")[0] in _DEPS_TOPO:
        return f"dependências ausentes — falta o extra [diarization] ({e})"
    primeira = (str(e) or type(e).__name__).splitlines()[0][:200]
    return (f"torch/pyannote instalados, mas um import interno falhou ({type(e).__name__}: "
            f"{primeira}) — não é falta do extra [diarization]: é a instalação dos componentes "
            "ou o empacotamento do app. Reinstale os componentes em Configurações → Sobre; "
            "se persistir, use Reportar erro")


@dataclass
class DiarizationResult:
    """Saída da diarização: trechos por voz + um embedding por voz (issue #1).

    embeddings mapeia o rótulo cru do pyannote (SPEAKER_00, …) ao vetor 256-d do
    wespeaker. Fica {} quando o modelo não expõe embeddings (pyannote legacy) —
    aí a separação por voz funciona, mas sem reconhecimento de quem é quem.
    """

    turns: list[Turn]
    embeddings: dict[str, list[float]] = field(default_factory=dict)


def _speaker_kwargs(cfg: Diarization, num_speakers: int | None) -> dict:
    """Argumentos de contagem de vozes para o pipeline do pyannote.

    num_speakers (informado pelo usuário ao fim da call) trava min=max=N e tem
    PRECEDÊNCIA sobre min/max_speakers — no pyannote, eles não têm efeito quando
    num_speakers é dado, então nunca os combinamos. Sem num_speakers, cai no
    min/max_speakers do config (0 = automático em cada um).
    """
    if num_speakers and int(num_speakers) >= 1:
        return {"num_speakers": int(num_speakers)}
    hi = int(cfg.max_speakers or 0)
    lo = int(getattr(cfg, "min_speakers", 0) or 0)
    kw: dict = {}
    if hi > 1:
        kw["max_speakers"] = hi
    if lo > 1:
        kw["min_speakers"] = min(lo, hi) if hi > 1 else lo  # mín nunca passa do máx
    return kw


def _classify_hf_error(e: Exception, model: str) -> str:
    """Traduz a exceção do pyannote/HF numa dica acionável. Heurística por substring
    (as exceções mudam entre versões) — quando não reconhece, devolve a 1ª linha crua."""
    msg = str(e) or type(e).__name__
    low = msg.lower()
    if any(s in low for s in ("401", "unauthorized", "invalid", "credential", "authentication")):
        return ("Token inválido ou sem permissão de leitura. Gere um token 'read' em "
                "hf.co/settings/tokens e cole de novo.")
    if any(s in low for s in ("403", "gated", "awaiting", "accept", "agree", "terms", "conditions", "access")):
        return (f"Falta aceitar os termos de '{model}' (e dos modelos relacionados) na sua conta "
                "do Hugging Face. Abra a página do modelo, aceite e teste de novo.")
    if any(s in low for s in ("connection", "timeout", "resolve", "network", "ssl", "getaddrinfo", "max retries")):
        return "Sem conexão com o Hugging Face. Verifique a internet/proxy e tente de novo."
    return f"Falha ao carregar o modelo: {msg.splitlines()[0][:200]}"


def test_token(model: str, token: str) -> tuple[bool, str]:
    """Valida token + termos carregando o pipeline num clique (#22), SEM precisar gravar
    uma call inteira. Devolve (ok, mensagem). A 1ª chamada baixa ~1-2 GB — rode em thread
    na UI. A classificação de erro é best-effort; em último caso devolve a mensagem crua."""
    if not (token or "").strip():
        return (False, "Informe o token do Hugging Face antes de testar.")
    model = (model or "").strip() or "pyannote/speaker-diarization-community-1"
    for pkg in _DEPS_TOPO:
        purge_stale_submodules(pkg)
    try:
        import warnings

        warnings.filterwarnings("ignore", message="(?s).*torchcodec.*")
        from pyannote.audio import Pipeline
    except Exception as e:  # noqa: BLE001 — ImportError E import interno quebrado (#197)
        msg = deps_error_message(e)
        if msg.startswith("dependências ausentes"):
            return (False, f"Diarização não instalada — falta o extra [diarization] (pyannote + torch). {e}")
        return (False, f"Diarização: {msg}")
    try:
        try:
            pipe = Pipeline.from_pretrained(model, token=token)
        except TypeError as e_token:  # token= é a API atual; use_auth_token só em versões antigas
            try:
                pipe = Pipeline.from_pretrained(model, use_auth_token=token)
            except TypeError:
                raise e_token  # não mascara o erro real do token= (#24)
    except Exception as e:  # noqa: BLE001 — classificamos e seguimos
        return (False, _classify_hf_error(e, model))
    if pipe is None:
        return (False, f"Token OK, mas os termos de '{model}' parecem não aceitos na sua conta do "
                       "Hugging Face. Abra a página do modelo e aceite os termos.")
    return (True, f"OK — modelo '{model}' carregado. Diarização pronta para usar.")


def diarize(wav: Path, cfg: Diarization, num_speakers: int | None = None,
            meta: dict | None = None, force_cpu: bool = False) -> DiarizationResult | None:
    """Trechos por voz do arquivo, ou None se desabilitado/indisponível (segue sem separar).

    num_speakers: nº de vozes remotas (loopback) informado pelo usuário — trava a
    diarização nesse número. None = automático (ou max_speakers do config).
    force_cpu: roda o pyannote em CPU mesmo com CUDA disponível (#115) — o caminho
    de recuperação após um erro de driver na GPU (`scribadev transcribe --cpu`).

    Devolve um DiarizationResult (turns + embeddings por voz) para o enrollment
    da issue #1 — ou None se a diarização não rodou.
    """
    if not cfg.enabled:
        return None

    def _fail(msg: str, *, exc: bool = False) -> None:
        # erro de diarização SEMPRE no log (process.log) + razão no meta, p/ o app
        # principal repetir no scriba.log central e a UI/diagnóstico verem (pedido do Allan)
        (log.exception if exc else log.error)("diarização: %s", msg)
        print(f"AVISO: diarização — {msg}")
        if meta is not None:
            meta["diarization_error"] = msg[:200]

    if not cfg.hf_token:
        _fail("habilitada sem token Hugging Face — configure na aba Transcrição")
        return None
    # um `import torch` anterior que falhou no meio (engolido por alguém) deixaria
    # este import morrer no falso circular import da #196 - limpa antes
    for pkg in _DEPS_TOPO:
        purge_stale_submodules(pkg)
    try:
        import warnings

        # o aviso de torchcodec/FFmpeg do pyannote despeja ~8 KB de traceback no
        # stderr; é inofensivo aqui (lemos o áudio nós mesmos em _load_waveform)
        warnings.filterwarnings("ignore", message="(?s).*torchcodec.*")
        # Apple Silicon (#203): op do pyannote sem kernel Metal cai na CPU em vez de
        # matar o processo. O torch lê a variável ao inicializar o backend MPS, por
        # isso ela entra ANTES do import (setdefault: quem já exportou manda).
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        import torch
        from pyannote.audio import Pipeline
    except Exception as e:  # noqa: BLE001 — ImportError E import interno quebrado (#196/#197)
        # a causa real vai inteira p/ o log (exc=True): é o que o diagnóstico precisa
        _fail(deps_error_message(e), exc=True)
        return None
    pipe = None
    ctx_broken = False  # erro de driver/contexto (#115): não tocar mais na GPU
    try:
        try:
            pipe = Pipeline.from_pretrained(cfg.model, token=cfg.hf_token)
        except TypeError as e_token:
            # token= é a API atual; só pyannote/huggingface_hub ANTIGOS usam use_auth_token.
            # Se o fallback também falhar (use_auth_token foi REMOVIDO nas versões novas),
            # propaga o erro do token= — o relevante — em vez de mascará-lo com o TypeError
            # do use_auth_token (issue #24).
            try:
                pipe = Pipeline.from_pretrained(cfg.model, use_auth_token=cfg.hf_token)
            except TypeError:
                raise e_token
        if pipe is None:
            _fail(f"modelo indisponível — confirme que aceitou os termos de {cfg.model} no "
                  "Hugging Face com a conta do token")
            return None
        # em que dispositivo o pyannote vai rodar, do mesmo jeito que a transcrição
        # imprime o dela (#190): "cuda" no log era só o faster-whisper, e um torch
        # build CPU passava batido - a #188 levou uma rodada inteira p/ descobrir.
        # No Apple Silicon o caminho é o Metal via MPS (#203): antes só havia o
        # ramo cuda e a separação de vozes rodava inteira em CPU (~0,55x do tempo
        # real num M5 Pro: 10 min p/ uma call de 20, contra 33 s da transcrição).
        device = pick_device(torch, force_cpu)
        if device == "cpu":
            if force_cpu:
                print("diarização em CPU (forçada com --cpu)")
            else:
                print(f"diarização em CPU (torch {torch.__version__} sem CUDA nem MPS)")
        else:
            print(f"diarização em {device_label(device, torch)}")
        if device == "cuda":
            pipe.to(torch.device("cuda"))
            # blinda contra o sysmem fallback do Windows (spill VRAM->RAM = freeze):
            # capa o allocator do PyTorch com uma margem livre, então VRAM apertada/
            # bloco pesado vira OOM capturável (cai em "Participantes"), não trava (issue #8).
            _cap_pyannote_vram()
        elif device == "mps":
            pipe.to(torch.device("mps"))
        audio = _load_waveform(wav)
        kwargs = _speaker_kwargs(cfg, num_speakers)
        chunk_s = max(0, int(getattr(cfg, "chunk_minutes", 3) or 0)) * 60
        dur = (audio["waveform"].shape[-1] / int(audio["sample_rate"])) if audio is not None else 0.0

        def _run(strict_first_block: bool):
            if audio is not None and chunk_s and dur > chunk_s:
                # Áudio longo: diariza em blocos p/ NÃO estourar a VRAM (o pico da
                # diarização é ~O(duração²) — a matriz de afinidade do clustering). As
                # vozes da PRÓPRIA call são re-ligadas pelo embedding (não depende de
                # conhecer ninguém). O nº de vozes informado vale aqui também (#198):
                # teto por bloco + redução das vozes globais ao N no fim.
                return _diarize_chunked(pipe, audio, int(audio["sample_rate"]), chunk_s,
                                        num_speakers=kwargs.get("num_speakers"),
                                        max_speakers=kwargs.get("max_speakers"),
                                        strict_first_block=strict_first_block)
            if "num_speakers" in kwargs:
                print(f"separando participantes por voz (fixo em {kwargs['num_speakers']} voz(es))...")
            else:
                print("separando participantes por voz...")
            return _run_pipe(pipe, audio if audio is not None else str(wav), kwargs)

        try:
            out = _run(strict_first_block=(device == "mps"))
        except _CudaContextError:
            raise
        except Exception as e:
            if device != "mps":
                raise
            # MPS (#203): o pyannote no Metal não tem a quilometragem do CUDA - um
            # op sem kernel que o fallback não cobre, ou um bug do backend, não pode
            # custar a separação de vozes inteira. Refaz em CPU (o resultado de
            # antes desta versão), com o motivo visível no process.log.
            log.warning("diarização em MPS falhou (%s); refazendo em CPU", e)
            print(f"AVISO: diarização em Metal (MPS) falhou ({e}); refazendo em CPU")
            pipe.to(torch.device("cpu"))
            _free_device_cache()
            device = "cpu"
            out = _run(strict_first_block=False)

        if out is None:
            _fail("não reconheci o retorno do pipeline (versão do pyannote?)")
            return None
        turns, embeddings = out
        voices = {label for *_x, label in turns}
        print(f"diarização: {len(voices)} voz(es) distintas em {len(turns)} trechos")
        return DiarizationResult(turns=turns, embeddings=embeddings)
    except _CudaContextError as e:
        # Erro de DRIVER/CONTEXTO (#115): abortar é a proteção — continuar
        # submetendo trabalho a um contexto corrompido escalou para TDR/perda
        # de vídeo em produção. A nota sai normal, sem separação de voz.
        ctx_broken = True
        _fail(f"erro de driver/contexto CUDA ({e}) — abortada para proteger a GPU; "
              "reprocesse com 'scribadev transcribe <pasta> --cpu' para diarizar em CPU")
        return None
    except Exception as e:
        _fail(f"falhou ({type(e).__name__}: {e}); seguindo com 'Participantes'", exc=True)
        return None
    finally:
        # libera o modelo + cache CUDA do pyannote LOGO após a diarização: senão ele
        # segura ~1-2 GB durante o resumo (claude -p) e o arquivamento (ffmpeg), que
        # rodam depois no MESMO processo do pipeline. (O contexto CUDA do torch em si,
        # ~0,5-1 GB, só sai quando o subprocesso encerra — aí é inevitável.)
        # Com o contexto QUEBRADO (#115), NADA de CUDA aqui: até empty_cache/uncap
        # submeteria chamadas ao driver ferido.
        if not ctx_broken:
            _uncap_pyannote_vram()  # restaura a fração do allocator (o cap valia só aqui)
        pipe = None
        if not ctx_broken:
            _free_device_cache()
        else:
            import gc

            gc.collect()  # solta o modelo sem tocar no driver ferido


def _extract_embeddings(result, annotation) -> dict[str, list[float]]:
    """{rótulo do pyannote → vetor de voz} a partir do DiarizeOutput (pyannote 4.x).

    speaker_embeddings vem como (n_vozes, dim) NA ORDEM de annotation.labels();
    casa um a um. Modos sem embeddings (pyannote legacy 3.x devolve Annotation
    crua) → {}, e a diarização segue sem reconhecer quem é quem.
    """
    emb = getattr(result, "speaker_embeddings", None)
    if emb is None:
        return {}
    try:
        import numpy as np

        out: dict[str, list[float]] = {}
        for i, label in enumerate(annotation.labels()):
            if i >= len(emb):
                break
            vec = np.asarray(emb[i], dtype=np.float32)
            # o wespeaker às vezes devolve NaN para uma voz com pouquíssima fala —
            # descarta (essa voz fica sem enrollment e segue como "Participante N")
            if vec.size and not bool(np.isnan(vec).any()):
                out[str(label)] = vec.tolist()
        return out
    except Exception:
        log.exception("não consegui extrair embeddings de voz")
        return {}


def _unwrap_result(result):
    """pyannote 4.0.5+ (batch inference) devolve um GERADOR lazy (1 item por arquivo); como
    só passamos um áudio, CONSOME o gerador e desembrulha p/ o item único (DiarizeOutput/
    Annotation). Lista/tupla de 1 item também. Senão passa direto (Annotation no 3.x/4.0.4).
    #24: o diagnóstico mostrou `tipo=generator` no log do reporter — não consumi-lo dava
    "0 voz(es) em 0 trechos"."""
    import types

    if isinstance(result, types.GeneratorType):
        result = list(result)  # consome o gerador -> lista de itens (normalmente 1)
    if isinstance(result, (list, tuple)) and len(result) == 1:
        return result[0]
    return result


def _describe_result(result) -> str:
    """Descrição curta do retorno do pipeline p/ o log de diagnóstico (#24): tipo, e p/
    lista/tupla o tamanho + o tipo e atributos do 1º item — foi assim que se achou o
    generator/lista do pyannote 4.0.5+ (o `dir()` de uma lista só mostra métodos, inútil)."""
    kind = type(result).__name__
    if isinstance(result, (list, tuple)):
        out = f"tipo={kind} len={len(result)}"
        if result:
            it = result[0]
            out += f" item0_tipo={type(it).__name__} item0_attrs={[a for a in dir(it) if not a.startswith('_')][:20]}"
        return out
    return f"tipo={kind} attrs={[a for a in dir(result) if not a.startswith('_')][:25]}"


def _run_pipe(pipe, audio, kwargs) -> tuple[list[Turn], dict[str, list[float]]] | None:
    """Roda o pipeline num áudio (dict ou caminho) e devolve (turns, embeddings),
    ou None se não reconheceu o retorno.

    Usa `pipe.apply(file)` (single) em vez de `pipe(file)`: em pyannote 4.0.5+ o `__call__`
    ganhou processamento em lote e passou a devolver um GERADOR/iterator mesmo p/ um arquivo
    só — o parser via "0 voz(es)" (#24). `apply()` vai direto ao DiarizeOutput/Annotation e
    tem a mesma assinatura no 4.0.4/4.0.6. Fallback p/ `pipe()` em versões sem `apply`."""
    run = getattr(pipe, "apply", pipe)
    result = _unwrap_result(run(audio, **kwargs))
    annotation = _extract_annotation(result)
    if annotation is None:
        log.warning("diarização: retorno do pyannote não reconhecido — %s", _describe_result(result))
        return None
    turns = [
        (float(turn.start), float(turn.end), str(label))
        for turn, _, label in annotation.itertracks(yield_label=True)
    ]
    return turns, _extract_embeddings(result, annotation)


def _mps_available(torch) -> bool:
    """Metal Performance Shaders utilizáveis? (Apple Silicon com torch de MPS, #203)"""
    try:
        mps = getattr(torch.backends, "mps", None)
        return bool(mps is not None and mps.is_available())
    except Exception:
        return False


def pick_device(torch, force_cpu: bool = False) -> str:
    """Onde o pyannote roda: 'cuda' | 'mps' (Metal no Apple Silicon, #203) | 'cpu'.
    A MESMA regra vale p/ o `doctor`, que antes só olhava o CUDA e reportava
    "CPU" num Mac que agora vai de Metal."""
    if force_cpu:
        return "cpu"
    try:
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "mps" if _mps_available(torch) else "cpu"


def device_label(device: str, torch=None) -> str:
    """Rótulo p/ log e doctor: 'cuda (NVIDIA ...)', 'GPU Metal (MPS)' ou 'CPU'."""
    if device == "cuda":
        name = "GPU"
        try:
            name = torch.cuda.get_device_name(0)
        except Exception:
            pass
        return f"cuda ({name})"
    if device == "mps":
        return "GPU Metal (MPS)"
    return "CPU"


def _free_device_cache() -> None:
    """Libera o cache do acelerador (CUDA ou MPS) - o pico de trabalho -, mantendo
    o modelo carregado. Nunca toca no torch se ninguém o importou (#196)."""
    try:
        import gc
        import sys

        gc.collect()
        torch = sys.modules.get("torch")
        if torch is None:
            return
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif _mps_available(torch):
            torch.mps.empty_cache()
    except Exception:
        pass


# ---- proteção de GPU (issue #115) -------------------------------------------
# OOM é recuperável (libera cache e pula o bloco); erro de DRIVER/CONTEXTO não é:
# o contexto CUDA fica corrompido e continuar submetendo kernels leva a TDR/perda
# de vídeo (aconteceu em produção: 85°C + cudaErrorUnknown em série no fim de uma
# diarização de ~112 min + driver travado = máquina desligada no botão).

class _CudaContextError(RuntimeError):
    """Contexto CUDA corrompido (erro de driver): abortar a diarização inteira e
    NÃO tocar mais na GPU neste processo (nem empty_cache)."""


_CTX_ERROR_MARKERS = (
    "cuda error", "cudaerror", "device-side assert", "illegal memory access",
    "unspecified launch failure", "misaligned address", "cudnn error",
)


def _is_cuda_context_error(e: BaseException) -> bool:
    """Erro de driver/contexto CUDA? (≠ OOM/alloc, que é recuperável por bloco)."""
    msg = str(e).lower()
    if "out of memory" in msg or "alloc" in msg:  # OOM e afins: recuperável
        return False
    try:
        import torch

        if isinstance(e, torch.cuda.OutOfMemoryError):
            return False
    except Exception:
        pass
    return any(m in msg for m in _CTX_ERROR_MARKERS)


# Pacing térmico entre blocos (#115): a diarização chunked mantém a GPU a 100%
# por dezenas de minutos em áudio longo. Um respiro por bloco + pausa quando a
# temperatura passa do limiar evita o full-throttle sustentado que derrubou o
# driver em produção. Tudo best-effort: sem nvidia-smi, fica só o respiro.
_BLOCK_YIELD_S = 0.5     # respiro fixo entre blocos
_TEMP_SOFT_C = 80        # acima disso, espera esfriar...
_TEMP_RESUME_C = 74      # ...até voltar para cá
_TEMP_MAX_WAIT_S = 60    # teto da espera (termômetro nunca trava o pipeline)
_MAX_CONSECUTIVE_FAILS = 3  # blocos falhando em série: desiste (não insiste na GPU ferida)


def _gpu_temperature() -> int | None:
    """Temperatura da GPU em °C via nvidia-smi (None se indisponível)."""
    import shutil
    import subprocess

    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


def _thermal_pause() -> None:
    """Respiro entre blocos; se a GPU estiver quente, espera esfriar (limitado)."""
    time.sleep(_BLOCK_YIELD_S)
    t = _gpu_temperature()
    if t is None or t < _TEMP_SOFT_C:
        return
    log.warning("diarização: GPU a %d°C — pausando até esfriar (<%d°C)", t, _TEMP_RESUME_C)
    print(f"GPU a {t}°C — pausa para resfriar antes do próximo bloco...")
    deadline = time.monotonic() + _TEMP_MAX_WAIT_S
    while time.monotonic() < deadline:
        time.sleep(5)
        t = _gpu_temperature()
        if t is None or t <= _TEMP_RESUME_C:
            return


# Margem de VRAM (MB) deixada LIVRE no device durante a diarização. O working-set
# medido do pyannote é ~3,3 GB/bloco; capar o allocator do PyTorch nesse teto faz um
# bloco pesado (ou VRAM já apertada por outra app) levantar um OutOfMemoryError
# CAPTURÁVEL — que diarize() degrada para "Participantes" — em vez de cair no fallback
# VRAM->RAM do Windows, que TRAVA a máquina e não se recupera. Diagnóstico: issue #8
# (não era vazamento nem fragmentação — o gatilho é VRAM livre baixa -> sysmem fallback).
_VRAM_KEEP_FREE_MB = 1024


def _cap_pyannote_vram(keep_free_mb: int = _VRAM_KEEP_FREE_MB) -> None:
    """Limita o allocator CUDA do PyTorch a deixar ~keep_free_mb livres no device.

    Só afeta o pool do PyTorch (pyannote); a transcrição (ctranslate2) usa memória
    própria e já liberou quando a diarização roda. Falha graciosamente (sem cap) se
    a API não existir ou a VRAM estiver indisponível."""
    try:
        import torch

        if not torch.cuda.is_available():
            return
        dev = torch.cuda.current_device()
        free, total = torch.cuda.mem_get_info(dev)
        held = torch.cuda.memory_reserved(dev)  # o que o allocator já segura (modelo)
        # teto do processo = o que já temos + (livre - margem); nunca abaixo do atual.
        cap = held + max(0, free - keep_free_mb * 1024 * 1024)
        frac = min(0.95, max(0.05, cap / total))
        torch.cuda.set_per_process_memory_fraction(frac, dev)
        log.info(
            "diarização: allocator CUDA capado em ~%.0f MB (frac %.2f; %.0f MB livres, margem %d MB)",
            cap / 1e6, frac, free / 1e6, keep_free_mb,
        )
    except Exception:
        log.debug("não consegui capar o allocator CUDA — seguindo sem cap", exc_info=True)


def _uncap_pyannote_vram() -> None:
    """Restaura a fração do allocator (1.0) ao fim da diarização."""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.set_per_process_memory_fraction(1.0, torch.cuda.current_device())
    except Exception:
        pass


def _cosine(a, b) -> float:
    import numpy as np

    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(np.dot(a, b) / (na * nb)) if na and nb else 0.0


# Limiar de cosseno p/ considerar a voz de dois blocos a MESMA pessoa. Conservador:
# o gap medido é enorme (mesma voz ~0,87 · vozes distintas ~0,11), então 0,5 fica
# folgado no meio — evita tanto fundir pessoas quanto fragmentar uma só.
_RELINK_THRESHOLD = 0.5


def _match_or_new_global(globals_: list, vec, threshold: float = _RELINK_THRESHOLD) -> str:
    """Liga o embedding `vec` a uma voz global da call (melhor cosseno ≥ limiar) ou
    cria uma nova. `globals_` é mutado: [centroide, n_amostras, id] por voz; o
    centroide da voz casada é atualizado (média acumulada)."""
    best_i, best_s = -1, -1.0
    for idx, (cen, _n, _gid) in enumerate(globals_):
        s = _cosine(vec, cen)
        if s > best_s:
            best_i, best_s = idx, s
    if best_i >= 0 and best_s >= threshold:
        cen, n, gid = globals_[best_i]
        globals_[best_i] = [(cen * n + vec) / (n + 1), n + 1, gid]
        return gid
    gid = f"G{len(globals_)}"
    globals_.append([vec, 1, gid])
    return gid


def _reduce_globals_to(globals_: list, n: int) -> dict[str, str]:
    """Funde as vozes globais mais parecidas até sobrarem `n` (#198).

    Aglomerativo simples: a cada passo o par de centroides de maior cosseno vira
    um só (média ponderada pelas amostras). `globals_` é mutado; devolve o mapa
    {id fundido → id que ficou} p/ reescrever os trechos. Com `n` ≥ vozes atuais
    não faz nada (não dá para SEPARAR o que o clustering juntou — só juntar).
    """
    mapa: dict[str, str] = {}
    while n >= 1 and len(globals_) > n:
        best = (-2.0, 0, 1)
        for i in range(len(globals_)):
            for j in range(i + 1, len(globals_)):
                s = _cosine(globals_[i][0], globals_[j][0])
                if s > best[0]:
                    best = (s, i, j)
        _s, i, j = best
        cen_i, n_i, gid_i = globals_[i]
        cen_j, n_j, gid_j = globals_[j]
        globals_[i] = [(cen_i * n_i + cen_j * n_j) / (n_i + n_j), n_i + n_j, gid_i]
        del globals_[j]
        # quem já apontava p/ gid_j passa a apontar p/ gid_i (fusões encadeadas)
        for k, v in list(mapa.items()):
            if v == gid_j:
                mapa[k] = gid_i
        mapa[gid_j] = gid_i
    return mapa


def _diarize_chunked(pipe, audio, sr: int, chunk_s: int, num_speakers: int | None = None,
                     max_speakers: int | None = None,
                     strict_first_block: bool = False) -> tuple[list[Turn], dict[str, list[float]]] | None:
    """Diariza áudio longo em blocos de `chunk_s` segundos, re-ligando as vozes
    entre blocos pelo embedding (cosseno). Cada bloco cabe na VRAM; entre blocos o
    cache CUDA é liberado — assim o pico fica por-bloco e nunca estoura. As vozes
    da própria call são re-ligadas, então funciona mesmo sem conhecer ninguém.

    num_speakers (#198): o nº informado pelo usuário era DESCARTADO neste caminho
    — e com chunk_minutes=3 (default) praticamente toda reunião real cai aqui, o
    que fazia a pergunta "quantas vozes?" só valer para calls de menos de 3 min.
    O `num_speakers` do pyannote é por chamada e um bloco pode ter só parte das
    vozes, então ele não pode ser fixado por bloco; o que vale é: (1) teto por
    bloco (`max_speakers=N`: um bloco nunca tem mais vozes que a call inteira) e
    (2) no fim, as vozes globais re-ligadas são REDUZIDAS a N fundindo os
    centroides mais parecidos (`_reduce_globals_to`). Sem num_speakers, só o
    max_speakers do config (se houver) vale como teto por bloco.

    strict_first_block (#203): num device sem quilometragem (MPS), o 1º bloco
    falhando não é "um bloco ruim" - é o backend. Em vez de pular e insistir até
    _MAX_CONSECUTIVE_FAILS (e devolver uma call quase sem vozes), deixa o erro
    subir p/ diarize() refazer tudo em CPU. Do 2º bloco em diante vale a
    resiliência de sempre."""
    import numpy as np

    wav = audio["waveform"]
    total = wav.shape[-1]
    chunk_n = max(1, int(chunk_s * sr))
    n_chunks = (total + chunk_n - 1) // chunk_n
    teto = int(num_speakers or max_speakers or 0)
    block_kwargs = {"max_speakers": teto} if teto >= 1 else {}
    fixo = f", fixo em {int(num_speakers)} voz(es)" if num_speakers else ""
    print(f"separando participantes por voz em {n_chunks} blocos de ~{chunk_s // 60} min "
          f"(áudio longo: evita estouro de VRAM{fixo})...")

    globals_: list = []          # [centroide(np), n_amostras, id global] por voz da call
    all_turns: list[Turn] = []
    consecutive_fails = 0
    for i in range(n_chunks):
        a, b = i * chunk_n, min((i + 1) * chunk_n, total)
        offset = a / sr
        # a diarização em blocos não imprime nada por bloco: sem este batimento a
        # sentinela (#188) tomaria uma diarização longa em CPU por travamento
        idlewatch.beat()
        try:
            out = _run_pipe(pipe, {"waveform": wav[:, a:b].clone(), "sample_rate": sr}, block_kwargs)
        except Exception as e:
            if _is_cuda_context_error(e):
                # Contexto CUDA corrompido (#115): NÃO submeter mais NADA à GPU —
                # nem empty_cache. Aborta tudo; diarize() degrada p/ "Participantes".
                raise _CudaContextError(f"bloco {i + 1}/{n_chunks}: {e}") from e
            if strict_first_block and i == 0:
                raise  # backend sem validação falhou de cara (#203): diarize() refaz em CPU
            # Resiliência por bloco: um bloco que falha (ex.: OOM sob VRAM apertada —
            # com o cap, o spill->freeze vira um OutOfMemoryError) NÃO derruba a call
            # inteira. Pula só este trecho (cai em "Participantes") e segue com os
            # demais. Antes, o erro subia e zerava TODA a diarização (issue #8).
            consecutive_fails += 1
            _free_device_cache()
            log.warning("diarização: bloco %d/%d falhou (%s); pulando este trecho", i + 1, n_chunks, e)
            if consecutive_fails >= _MAX_CONSECUTIVE_FAILS:
                # Insistir numa GPU que só falha piora o estado do driver (#115):
                # para aqui e mantém o que já foi separado (blocos bons ficam;
                # o resto cai em "Participantes").
                log.warning("diarização: %d blocos consecutivos falharam — parando; "
                            "trechos já separados são mantidos", consecutive_fails)
                print(f"AVISO: diarização interrompida após {consecutive_fails} falhas seguidas — "
                      "os trechos já separados são mantidos")
                break
            continue
        consecutive_fails = 0
        _free_device_cache()     # solta o pico de trabalho do bloco antes do próximo
        if i + 1 < n_chunks:
            _thermal_pause()     # respiro/espera térmica entre blocos (#115)
        if out is None:
            continue
        turns, embs = out
        local_to_global = {
            label: _match_or_new_global(globals_, np.asarray(vec, dtype=np.float32))
            for label, vec in embs.items()
        }
        for s, e, label in turns:
            # voz sem embedding (NaN/silêncio): id isolado do bloco — não re-ligável
            all_turns.append((s + offset, e + offset, local_to_global.get(label, f"c{i}_{label}")))

    if num_speakers and len(globals_) > int(num_speakers):
        # mais vozes globais que o informado: o clustering fragmentou alguém entre
        # blocos (voz "drifta" abaixo do limiar de re-ligação) - reduz ao N (#198)
        antes = len(globals_)
        mapa = _reduce_globals_to(globals_, int(num_speakers))
        all_turns = [(s, e, mapa.get(lab, lab)) for s, e, lab in all_turns]
        log.info("diarização: %d vozes globais reduzidas a %d (nº informado pelo usuário)",
                 antes, len(globals_))
        print(f"vozes re-ligadas entre blocos: {antes} -> {len(globals_)} (nº informado)")
    elif num_speakers and len(globals_) < int(num_speakers):
        log.info("diarização: só %d voz(es) global(is) para %d informadas - seguindo com o que há",
                 len(globals_), int(num_speakers))

    all_turns.sort(key=lambda t: t[0])
    embeddings = {gid: cen.tolist() for cen, _n, gid in globals_}
    return all_turns, embeddings


def _extract_annotation(result):
    """Acha a Annotation no retorno do pipeline (a API muda entre versões do pyannote).

    3.x/4.0.4: Annotation direto. 4.x: DiarizeOutput com `.speaker_diarization`. 4.0.5+:
    batch inference devolve LISTA (já desembrulhada em _unwrap_result; aqui tratamos
    lista/dict por robustez, caso venham vários itens). None se nada bater."""
    if result is None:
        return None
    if hasattr(result, "itertracks"):  # Annotation direto
        return result
    for attr in ("speaker_diarization", "diarization", "annotation"):  # DiarizeOutput & cia
        candidate = getattr(result, attr, None)
        if candidate is not None and hasattr(candidate, "itertracks"):
            return candidate
    if isinstance(result, (list, tuple)):  # batch inference: 1+ itens
        for item in result:
            got = _extract_annotation(item)
            if got is not None:
                return got
        return None
    if isinstance(result, dict):
        for item in result.values():
            got = _extract_annotation(item)
            if got is not None:
                return got
        return None
    # último recurso: qualquer atributo público que pareça uma Annotation
    for attr in dir(result):
        if attr.startswith("_"):
            continue
        try:
            candidate = getattr(result, attr, None)
        except Exception:
            continue
        if candidate is not None and hasattr(candidate, "itertracks"):
            return candidate
    return None


def _read_pcm_wav(path: Path):
    """Lê um WAV PCM 16-bit como tensor (canal, tempo) — sem torchcodec/FFmpeg."""
    import wave as wave_mod

    import numpy as np
    import torch

    with wave_mod.open(str(path)) as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        frames = w.readframes(w.getnframes())
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return {"waveform": torch.from_numpy(data).unsqueeze(0), "sample_rate": rate}


def _load_waveform(wav: Path):
    """Áudio como tensor (canal, tempo), evitando o torchcodec/FFmpeg do pyannote.

    WAV PCM é lido direto. Áudio comprimido (opus/flac de uma pasta já arquivada,
    numa re-transcrição) é decodificado via ffmpeg DIRETO para a memória (PCM s16le
    por pipe) — sem WAV temporário em disco, sem I/O extra nem risco de arquivo órfão.
    """
    try:
        return _read_pcm_wav(wav)
    except Exception:
        pass  # não é WAV PCM legível — tenta o ffmpeg abaixo

    from . import util

    ff = util.ffmpeg_command()
    if ff is None:
        log.warning("sem ffmpeg para decodificar %s; deixando o pyannote tentar", wav.name)
        return None

    import subprocess

    import numpy as np
    import torch

    try:
        r = subprocess.run(
            ff + ["-hide_banner", "-loglevel", "error", "-i", str(wav),
                  "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
            check=True, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        log.warning("não consegui decodificar %s (%s); deixando o pyannote tentar", wav.name, e)
        return None

    # mesmo formato do _read_pcm_wav: s16le mono 16 kHz -> float32 normalizado (1, N)
    data = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    return {"waveform": torch.from_numpy(data).unsqueeze(0), "sample_rate": 16000}


def assign_speakers(segments: list[Segment], turns: list[Turn]) -> tuple[dict[str, list[Segment]], dict[str, int]]:
    """Rotula cada segmento transcrito com a voz de maior sobreposição temporal.

    Vozes viram "Participante 1/2/3" na ordem em que aparecem na reunião;
    segmentos sem sobreposição com voz nenhuma caem em "Participantes".

    Retorna (grupos, ordem), onde `ordem` mapeia o rótulo cru do pyannote
    (SPEAKER_00, …) ao número N de "Participante N" — usado para casar cada
    participante ao seu embedding no enrollment de voz (#1).
    """
    order: dict[str, int] = {}
    grouped: dict[str, list[Segment]] = {}
    for seg in segments:
        best_label, best_overlap = None, 0.0
        for start, end, label in turns:
            overlap = min(seg.end, end) - max(seg.start, start)
            if overlap > best_overlap:
                best_label, best_overlap = label, overlap
        if best_label is None:
            name = "Participantes"
        else:
            if best_label not in order:
                order[best_label] = len(order) + 1
            name = f"Participante {order[best_label]}"
        grouped.setdefault(name, []).append(seg)
    return grouped, order
