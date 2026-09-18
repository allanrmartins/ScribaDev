"""Recorte de fala pelo VAD (Silero) ANTES de transcrever (#202).

O faster-whisper aplica o Silero por dentro (`vad_filter=True`): transcreve só os
trechos com voz e devolve os tempos já no relógio original. O mlx-whisper não tem
nada disso, e um stream de microfone numa call em que a pessoa mais escuta do
que fala é 80-90% de silêncio: o Whisper preenche cada janela de 30 s sem fala
com texto inventado ("A CIDADE NO BRASIL" x17 num mic de 19 min). Os limiares
internos (`no_speech_threshold`, `logprob_threshold`, `hallucination_silence_
threshold`) reduzem mas não zeram, porque o modelo continua decodificando a
janela; `clip_timestamps` piora (preenche cada clipe até a janela cheia).

O que zera é não entregar o silêncio ao modelo: detectar a fala com o MESMO
Silero que o faster-whisper usa (já vem no pacote, sem dependência nova),
concatenar só os trechos com voz e, depois, devolver os tempos dos segmentos ao
relógio original - o merge e a diarização dependem deles. Fica mais rápido, não
mais lento: transcreve 2,7 min em vez de 19,5.

Funções puras (recorte e remapeamento) separadas do I/O, testáveis sem áudio real.
"""

from __future__ import annotations

import bisect
import logging
from pathlib import Path

from .transcriber import Segment

log = logging.getLogger("scriba.vadcut")

SAMPLE_RATE = 16000


def load_audio(path: Path, sample_rate: int = SAMPLE_RATE):
    """Áudio mono float32 a `sample_rate` Hz (o formato que o Whisper consome),
    decodificado pelo mesmo caminho do faster-whisper (PyAV) - aceita WAV e o
    opus/flac de uma pasta já arquivada."""
    from faster_whisper.audio import decode_audio

    return decode_audio(str(path), sampling_rate=sample_rate)


def detect_speech(audio, sample_rate: int = SAMPLE_RATE, params: dict | None = None) -> list[dict]:
    """Trechos com voz, em AMOSTRAS: [{"start": int, "end": int}, ...], em ordem.
    `params` = os mesmos ajustes opcionais do faster-whisper (`threshold`,
    `min_silence_duration_ms`), p/ a calibragem do usuário valer nos dois motores."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    opts = VadOptions(**(params or {}))
    chunks = get_speech_timestamps(audio, opts, sampling_rate=sample_rate)
    return [{"start": int(c["start"]), "end": int(c["end"])} for c in chunks]


def concat_speech(audio, chunks: list[dict]):
    """Só os trechos com voz, emendados (o áudio "comprimido" que vai ao modelo)."""
    import numpy as np

    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate([audio[c["start"]:c["end"]] for c in chunks])


class TimeMap:
    """Relógio comprimido (áudio recortado) -> relógio original (stream inteiro)."""

    def __init__(self, chunks: list[dict], sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self.orig_start: list[int] = []   # início de cada trecho no original (amostras)
        self.cut_end: list[int] = []      # fim de cada trecho no comprimido (amostras)
        acc = 0
        for c in chunks:
            self.orig_start.append(int(c["start"]))
            acc += int(c["end"]) - int(c["start"])
            self.cut_end.append(acc)

    def _index(self, sample: int, is_end: bool) -> int:
        if not self.cut_end:
            return -1
        # fim de segmento que cai EXATAMENTE na emenda pertence ao trecho anterior
        # (senão herdaria o salto de silêncio do próximo e o fim vinha depois do
        # início do segmento seguinte)
        if is_end:
            i = bisect.bisect_left(self.cut_end, sample)
        else:
            i = bisect.bisect_right(self.cut_end, sample)
        return min(i, len(self.cut_end) - 1)

    def to_original(self, t: float, is_end: bool = False) -> float:
        """Segundos no comprimido -> segundos no original."""
        sample = int(round(t * self.sr))
        i = self._index(sample, is_end)
        if i < 0:
            return t
        cut_start = self.cut_end[i - 1] if i > 0 else 0
        # dentro do trecho: desloca; passou do fim do último trecho (o Whisper
        # arredonda p/ cima): gruda no fim real do último trecho
        offset = min(max(sample - cut_start, 0), self.cut_end[i] - cut_start)
        return round((self.orig_start[i] + offset) / self.sr, 2)


def restore_segments(segments: list[Segment], chunks: list[dict],
                     sample_rate: int = SAMPLE_RATE) -> list[Segment]:
    """Segmentos transcritos no áudio recortado -> tempos do stream original."""
    tm = TimeMap(chunks, sample_rate)
    out: list[Segment] = []
    for s in segments:
        start = tm.to_original(s.start)
        end = max(start, tm.to_original(s.end, is_end=True))
        out.append(Segment(start=start, end=end, text=s.text))
    return out


def describe(chunks: list[dict], total_samples: int, sample_rate: int = SAMPLE_RATE) -> str:
    """Uma linha p/ o process.log: quanto do stream era fala."""
    speech = sum(c["end"] - c["start"] for c in chunks) / sample_rate
    total = total_samples / sample_rate
    return (f"fala: {speech / 60:.1f} de {total / 60:.1f} min em {len(chunks)} trecho(s) "
            f"- silêncio recortado antes de transcrever")
