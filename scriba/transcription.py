"""Seam de transcrição (STT): `TranscriptionProvider` (Protocol) + fábrica.

Generaliza o motor de transcrição atrás de uma interface estrutural. Hoje só
existe o provider local (faster-whisper, em `transcriber.py`); a fábrica
`make_transcriber` é o ÚNICO ponto onde um futuro 2º backend (ex.: STT na nuvem)
entraria — sem mexer no `pipeline`. A diarização, o offset e o `merge` ficam na
orquestração do pipeline e funcionam para qualquer provider. (issue #13)
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from .config import Whisper
from .transcriber import Segment, Transcriber


@runtime_checkable
class TranscriptionProvider(Protocol):
    """Contrato de um motor de transcrição: WAV de um stream → segmentos (tempos
    RELATIVOS ao stream; o offset/merge são aplicados pelo pipeline)."""

    device_used: str | None

    def ensure_loaded(self) -> str:
        """Prepara o motor e devolve o device usado ('cuda' ou 'cpu')."""
        ...

    def transcribe(self, wav: Path, on_progress: Callable[[float], None] | None = None) -> list[Segment]:
        ...

    def close(self) -> None:
        """Libera recursos (modelo/VRAM) entre reuniões."""
        ...


def effective_engine(cfg: Whisper, force_cpu: bool = False) -> str:
    """Qual motor `make_transcriber` vai construir de fato p/ este config nesta
    máquina: 'cloud' | 'mlx' | 'faster-whisper'. É a ÚNICA regra de escolha - o
    `doctor` usa a mesma, p/ reportar o caminho efetivo e não só o que está
    instalado (#202: um engine desviado p/ o faster-whisper aparecia como "Metal")."""
    import platform
    import sys

    engine = (cfg.engine or "local").strip().lower()
    if engine == "cloud":
        return "cloud"
    if (not force_cpu and engine in ("local", "mlx")
            and sys.platform == "darwin" and platform.machine() == "arm64"):
        from .stt_mlx import mlx_disponivel

        if engine == "mlx" or mlx_disponivel():
            return "mlx"
    return "faster-whisper"


def make_transcriber(cfg: Whisper, force_cpu: bool = False) -> TranscriptionProvider:
    """Constrói o provider de transcrição conforme `cfg.engine`: 'cloud' → STT na
    nuvem (Groq/OpenAI-compat); 'mlx' (ou 'local' num mac Apple Silicon com
    mlx_whisper instalado) → Metal via MLX (#104, M5); senão o faster-whisper local.
    Com force_cpu o MLX é pulado — quem pediu CPU ganha CPU."""
    kind = effective_engine(cfg, force_cpu)
    if kind == "cloud":
        from .stt_cloud import CloudTranscriptionProvider

        return CloudTranscriptionProvider(cfg)
    if kind == "mlx":
        from .stt_mlx import MlxWhisperProvider

        return MlxWhisperProvider(cfg)
    return Transcriber(cfg, force_cpu=force_cpu)
