"""Mescla as transcrições do mic ("Eu") e do loopback ("Participantes") numa linha do tempo."""

from __future__ import annotations

from dataclasses import dataclass

from .transcriber import Segment

MAX_GAP_SECONDS = 2.0  # pausa máxima para fundir falas consecutivas do mesmo falante
MAX_TURN_CHARS = 600


@dataclass
class Turn:
    start: float
    end: float
    speaker: str
    text: str


def merge(by_speaker: dict[str, tuple[list[Segment], float]]) -> list[Turn]:
    """Recebe {falante: (segmentos, offset_do_stream_em_segundos)} e intercala por tempo."""
    raw: list[Turn] = []
    for speaker, (segments, offset) in by_speaker.items():
        last_text = None
        for s in segments:
            text = s.text.strip()
            if not text:
                continue
            if text == last_text:
                continue  # alucinação clássica do Whisper: mesmo segmento repetido
            last_text = text
            raw.append(Turn(s.start + offset, s.end + offset, speaker, text))
    raw.sort(key=lambda t: t.start)

    merged: list[Turn] = []
    for t in raw:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and prev.speaker == t.speaker
            and t.start - prev.end <= MAX_GAP_SECONDS
            and len(prev.text) + len(t.text) < MAX_TURN_CHARS
        ):
            prev.text += " " + t.text
            prev.end = max(prev.end, t.end)
        else:
            merged.append(Turn(t.start, t.end, t.speaker, t.text))
    return merged


def fmt_ts(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def render_markdown(turns: list[Turn]) -> str:
    return "\n\n".join(f"**[{fmt_ts(t.start)}] {t.speaker}:** {t.text}" for t in turns)
