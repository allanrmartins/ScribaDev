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
    PRECEDÊNCIA sobre max_speakers — no pyannote, max_speakers não tem efeito
    quando num_speakers é dado, então nunca combinamos os dois. Sem num_speakers,
    cai no max_speakers do config (0 = automático).
    """
    if num_speakers and int(num_speakers) >= 1:
        return {"num_speakers": int(num_speakers)}
    if cfg.max_speakers and int(cfg.max_speakers) > 1:
        return {"max_speakers": int(cfg.max_speakers)}
    return {}


def diarize(wav: Path, cfg: Diarization, num_speakers: int | None = None) -> DiarizationResult | None:
    """Trechos por voz do arquivo, ou None se desabilitado/indisponível (segue sem separar).

    num_speakers: nº de vozes remotas (loopback) informado pelo usuário — trava a
    diarização nesse número. None = automático (ou max_speakers do config).

    Devolve um DiarizationResult (turns + embeddings por voz) para o enrollment
    da issue #1 — ou None se a diarização não rodou.
    """
    if not cfg.enabled:
        return None
    if not cfg.hf_token:
        print("AVISO: diarização habilitada sem hf_token — pulando (configure na aba Gravação)")
        return None
    try:
        import warnings

        # o aviso de torchcodec/FFmpeg do pyannote despeja ~8 KB de traceback no
        # stderr; é inofensivo aqui (lemos o áudio nós mesmos em _load_waveform)
        warnings.filterwarnings("ignore", message="(?s).*torchcodec.*")
        import torch
        from pyannote.audio import Pipeline
    except ImportError as e:
        log.warning("diarização indisponível: %s", e)
        print(f"AVISO: diarização habilitada mas dependências ausentes ({e})")
        return None
    pipe = None
    try:
        try:
            pipe = Pipeline.from_pretrained(cfg.model, token=cfg.hf_token)
        except TypeError:  # versões antigas usam use_auth_token
            pipe = Pipeline.from_pretrained(cfg.model, use_auth_token=cfg.hf_token)
        if pipe is None:
            print(
                "AVISO: modelo de diarização indisponível — confirme que aceitou os termos "
                f"de {cfg.model} no Hugging Face com a conta do token"
            )
            return None
        if torch.cuda.is_available():
            pipe.to(torch.device("cuda"))
        kwargs = _speaker_kwargs(cfg, num_speakers)
        if "num_speakers" in kwargs:
            print(f"separando participantes por voz (fixo em {kwargs['num_speakers']} voz(es))...")
        else:
            print("separando participantes por voz...")
        audio = _load_waveform(wav)
        result = pipe(audio, **kwargs) if audio is not None else pipe(str(wav), **kwargs)
        annotation = _extract_annotation(result)
        if annotation is None:
            print(f"AVISO: não reconheci o retorno da diarização ({type(result).__name__})")
            return None
        turns = [
            (float(turn.start), float(turn.end), str(label))
            for turn, _, label in annotation.itertracks(yield_label=True)
        ]
        voices = {label for *_x, label in turns}
        print(f"diarização: {len(voices)} voz(es) distintas em {len(turns)} trechos")
        return DiarizationResult(turns=turns, embeddings=_extract_embeddings(result, annotation))
    except Exception as e:
        log.exception("diarização falhou")
        print(f"AVISO: diarização falhou ({e}); seguindo com 'Participantes'")
        return None
    finally:
        # libera o modelo + cache CUDA do pyannote LOGO após a diarização: senão ele
        # segura ~1-2 GB durante o resumo (claude -p) e o arquivamento (ffmpeg), que
        # rodam depois no MESMO processo do pipeline. (O contexto CUDA do torch em si,
        # ~0,5-1 GB, só sai quando o subprocesso encerra — aí é inevitável.)
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


def _extract_annotation(result):
    """Acha a Annotation no retorno do pipeline (a API mudou entre pyannote 3.x e 4.x).

    3.x devolve a Annotation direto; 4.x devolve um objeto (ex.: DiarizeOutput)
    com ela dentro.
    """
    if hasattr(result, "itertracks"):
        return result
    for attr in ("speaker_diarization", "diarization", "annotation"):
        candidate = getattr(result, attr, None)
        if candidate is not None and hasattr(candidate, "itertracks"):
            return candidate
    # último recurso: qualquer atributo público que pareça uma Annotation
    for attr in dir(result):
        if attr.startswith("_"):
            continue
        candidate = getattr(result, attr, None)
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
    numa re-transcrição) é decodificado via ffmpeg para um WAV 16 kHz mono temporário.
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

    import os
    import subprocess
    import tempfile

    tmp = Path(tempfile.gettempdir()) / f"scriba_dz_{os.getpid()}_{wav.stem}.wav"
    try:
        subprocess.run(
            ff + ["-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
                  "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(tmp)],
            check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return _read_pcm_wav(tmp)
    except Exception as e:
        log.warning("não consegui decodificar %s (%s); deixando o pyannote tentar", wav.name, e)
        return None
    finally:
        tmp.unlink(missing_ok=True)


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
