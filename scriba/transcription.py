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


def make_transcriber(cfg: Whisper, force_cpu: bool = False) -> TranscriptionProvider:
    """Constrói o provider de transcrição conforme `cfg.engine`: 'cloud' → STT na
    nuvem (Groq/OpenAI-compat); 'mlx' (ou 'local' num mac Apple Silicon com
    mlx_whisper instalado) → Metal via MLX (#104, M5); senão o faster-whisper local.
    Com force_cpu o MLX é pulado — quem pediu CPU ganha CPU."""
    import platform
    import sys

    engine = (cfg.engine or "local").strip().lower()
    if engine == "cloud":
        from .stt_cloud import CloudTranscriptionProvider

        return CloudTranscriptionProvider(cfg)
    if (not force_cpu and engine in ("local", "mlx")
            and sys.platform == "darwin" and platform.machine() == "arm64"):
        from .stt_mlx import MlxWhisperProvider, mlx_disponivel

        if engine == "mlx" or mlx_disponivel():
            return MlxWhisperProvider(cfg)
    return Transcriber(cfg, force_cpu=force_cpu)
