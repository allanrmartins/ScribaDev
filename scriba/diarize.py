"""Diarização local (pyannote.audio): separa os participantes remotos por voz.

Tudo roda na máquina (GPU se houver). O modelo oficial é "gated" no Hugging Face:
o usuário precisa de um token de leitura gratuito e de aceitar os termos na
página do modelo — depois disso, o download acontece uma vez e o resto é offline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import Diarization
from .transcriber import Segment

log = logging.getLogger("scriba.diarize")

Turn = tuple[float, float, str]  # (início, fim, rótulo da voz)


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
    try:
        import warnings

        warnings.filterwarnings("ignore", message="(?s).*torchcodec.*")
        from pyannote.audio import Pipeline
    except ImportError as e:
        return (False, f"Diarização não instalada — falta o extra [diarization] (pyannote + torch). {e}")
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
            meta: dict | None = None) -> DiarizationResult | None:
    """Trechos por voz do arquivo, ou None se desabilitado/indisponível (segue sem separar).

    num_speakers: nº de vozes remotas (loopback) informado pelo usuário — trava a
    diarização nesse número. None = automático (ou max_speakers do config).

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
    try:
        import warnings

        # o aviso de torchcodec/FFmpeg do pyannote despeja ~8 KB de traceback no
        # stderr; é inofensivo aqui (lemos o áudio nós mesmos em _load_waveform)
        warnings.filterwarnings("ignore", message="(?s).*torchcodec.*")
        import torch
        from pyannote.audio import Pipeline
    except ImportError as e:
        _fail(f"dependências ausentes — falta o extra [diarization] ({e})")
        return None
    pipe = None
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
        if torch.cuda.is_available():
            pipe.to(torch.device("cuda"))
            # blinda contra o sysmem fallback do Windows (spill VRAM->RAM = freeze):
            # capa o allocator do PyTorch com uma margem livre, então VRAM apertada/
            # bloco pesado vira OOM capturável (cai em "Participantes"), não trava (issue #8).
            _cap_pyannote_vram()
        audio = _load_waveform(wav)
        kwargs = _speaker_kwargs(cfg, num_speakers)
        chunk_s = max(0, int(getattr(cfg, "chunk_minutes", 3) or 0)) * 60
        dur = (audio["waveform"].shape[-1] / int(audio["sample_rate"])) if audio is not None else 0.0

        if audio is not None and chunk_s and dur > chunk_s:
            # Áudio longo: diariza em blocos p/ NÃO estourar a VRAM (o pico da
            # diarização é ~O(duração²) — a matriz de afinidade do clustering). As
            # vozes da PRÓPRIA call são re-ligadas pelo embedding (não depende de
            # conhecer ninguém). num_speakers não se aplica por bloco.
            out = _diarize_chunked(pipe, audio, int(audio["sample_rate"]), chunk_s)
        else:
            if "num_speakers" in kwargs:
                print(f"separando participantes por voz (fixo em {kwargs['num_speakers']} voz(es))...")
            else:
                print("separando participantes por voz...")
            out = _run_pipe(pipe, audio if audio is not None else str(wav), kwargs)

        if out is None:
            _fail("não reconheci o retorno do pipeline (versão do pyannote?)")
            return None
        turns, embeddings = out
        voices = {label for *_x, label in turns}
        print(f"diarização: {len(voices)} voz(es) distintas em {len(turns)} trechos")
        return DiarizationResult(turns=turns, embeddings=embeddings)
    except Exception as e:
        _fail(f"falhou ({type(e).__name__}: {e}); seguindo com 'Participantes'", exc=True)
        return None
    finally:
        # libera o modelo + cache CUDA do pyannote LOGO após a diarização: senão ele
        # segura ~1-2 GB durante o resumo (claude -p) e o arquivamento (ffmpeg), que
        # rodam depois no MESMO processo do pipeline. (O contexto CUDA do torch em si,
        # ~0,5-1 GB, só sai quando o subprocesso encerra — aí é inevitável.)
        _uncap_pyannote_vram()  # restaura a fração do allocator (o cap valia só aqui)
        pipe = None
        try:
            import gc

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


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


def _free_cuda() -> None:
    """Libera o cache CUDA (o pico de trabalho), mantendo o modelo carregado."""
    try:
        import gc

        import torch

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


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


def _diarize_chunked(pipe, audio, sr: int, chunk_s: int) -> tuple[list[Turn], dict[str, list[float]]] | None:
    """Diariza áudio longo em blocos de `chunk_s` segundos, re-ligando as vozes
    entre blocos pelo embedding (cosseno). Cada bloco cabe na VRAM; entre blocos o
    cache CUDA é liberado — assim o pico fica por-bloco e nunca estoura. As vozes
    da própria call são re-ligadas, então funciona mesmo sem conhecer ninguém."""
    import numpy as np

    wav = audio["waveform"]
    total = wav.shape[-1]
    chunk_n = max(1, int(chunk_s * sr))
    n_chunks = (total + chunk_n - 1) // chunk_n
    print(f"separando participantes por voz em {n_chunks} blocos de ~{chunk_s // 60} min "
          "(áudio longo: evita estouro de VRAM)...")

    globals_: list = []          # [centroide(np), n_amostras, id global] por voz da call
    all_turns: list[Turn] = []
    for i in range(n_chunks):
        a, b = i * chunk_n, min((i + 1) * chunk_n, total)
        offset = a / sr
        try:
            out = _run_pipe(pipe, {"waveform": wav[:, a:b].clone(), "sample_rate": sr}, {})
        except Exception as e:
            # Resiliência por bloco: um bloco que falha (ex.: OOM sob VRAM apertada —
            # com o cap, o spill->freeze vira um OutOfMemoryError) NÃO derruba a call
            # inteira. Pula só este trecho (cai em "Participantes") e segue com os
            # demais. Antes, o erro subia e zerava TODA a diarização (issue #8).
            _free_cuda()
            log.warning("diarização: bloco %d/%d falhou (%s); pulando este trecho", i + 1, n_chunks, e)
            continue
        _free_cuda()             # solta o pico de trabalho do bloco antes do próximo
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
