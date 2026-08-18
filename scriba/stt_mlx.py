"""Transcrição local acelerada por Metal (Apple Silicon) via mlx-whisper (#104, M5).

Provider do seam `transcription.TranscriptionProvider`, escolhido pela fábrica
`make_transcriber` quando engine=local roda num mac arm64 com mlx_whisper
instalado (ou engine="mlx" explícito). Mapeia o nome de modelo do faster-whisper
("large-v3-turbo") para o repo MLX equivalente ("mlx-community/whisper-large-v3-turbo");
um repo explícito (com "/") passa direto.

Diferenças vs faster-whisper aceitas no plano:
- sem parâmetro `hotwords` → o vocabulário vai como `initial_prompt` (validado no
  spike M2 com a fixture pt-BR);
- sem VAD Silero embutido → risco de alucinação em silêncio longo; o dedup do
  merge (merge.py) pega o caso clássico; calibragem fica p/ uso real.

Falha em runtime (repo inexistente, MLX quebrado, OOM) cai para o faster-whisper
em CPU — espelho do fallback CUDA→CPU do transcriber (transcriber.py:54-70).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from .config import Whisper
from .transcriber import Segment, Transcriber

log = logging.getLogger("scriba.stt_mlx")


def mlx_disponivel() -> bool:
    """mlx_whisper instalado neste ambiente? (a fábrica usa p/ decidir sem importar)"""
    import importlib.util

    try:
        return importlib.util.find_spec("mlx_whisper") is not None
    except Exception:
        return False


class MlxWhisperProvider:
    """Satisfaz TranscriptionProvider (device_used/ensure_loaded/transcribe/close)."""

    def __init__(self, cfg: Whisper):
        self.cfg = cfg
        self.device_used: str | None = None
        self._fallback: Transcriber | None = None  # faster-whisper CPU, criado só se precisar

    def _repo(self) -> str:
        name = (self.cfg.model or "large-v3-turbo").strip()
        if "/" in name:  # repo HF explícito
            return name
        return f"mlx-community/whisper-{name}"

    def ensure_loaded(self) -> str:
        """Confirma o mlx_whisper importável e devolve o device. O download/carga do
        modelo acontece na primeira transcribe (o mlx_whisper cacheia por repo).

        Import quebrado cai para faster-whisper CPU (mesmo fallback do transcribe):
        `mlx_disponivel()` só checa o find_spec, e no bundle congelado o pacote
        EXISTE mas o dlopen pode falhar (dylib do mlx faltando) — deixar o ImportError
        subir matava a transcrição inteira em vez de perder só a aceleração."""
        if self._fallback is not None:
            return self._fallback.ensure_loaded()
        try:
            import mlx_whisper  # noqa: F401
        except Exception as e:
            log.warning("MLX indisponível (%s); usando faster-whisper em CPU", e)
            print(f"AVISO: MLX/Metal indisponível ({e}); usando CPU")
            self._fallback = Transcriber(self.cfg, force_cpu=True)
            self.device_used = self._fallback.ensure_loaded()
            return self.device_used
        self.device_used = "metal"
        return self.device_used

    def transcribe(self, wav: Path, on_progress: Callable[[float], None] | None = None) -> list[Segment]:
        if self._fallback is not None:
            return self._fallback.transcribe(wav, on_progress)
        try:
            self.ensure_loaded()
            import mlx_whisper

            result = mlx_whisper.transcribe(
                str(wav),
                path_or_hf_repo=self._repo(),
                language=self.cfg.language or None,
                initial_prompt=self.cfg.hotwords or None,  # hotwords via prompt (sem param dedicado)
                condition_on_previous_text=False,
            )
        except Exception as e:
            # espelho do fallback CUDA→CPU: deixa o motivo visível e refaz em CPU
            log.warning("MLX falhou em runtime (%s); refazendo com faster-whisper em CPU", e)
            print(f"AVISO: MLX/Metal falhou ({e}); usando CPU")
            self._fallback = Transcriber(self.cfg, force_cpu=True)
            self.device_used = self._fallback.ensure_loaded()
            return self._fallback.transcribe(wav, on_progress)
        out: list[Segment] = []
        for s in result.get("segments", []):
            text = (s.get("text") or "").strip()
            if text:
                out.append(Segment(start=float(s["start"]), end=float(s["end"]), text=text))
            if on_progress:
                on_progress(float(s.get("end", 0.0)))
        return out

    def close(self) -> None:
        """Solta o modelo do cache do mlx_whisper (segura a memória unificada entre
        reuniões) — melhor esforço: API interna da lib, nunca quebra o pipeline."""
        if self._fallback is not None:
            self._fallback.close()
            self._fallback = None
        try:
            from mlx_whisper import load_models

            load_models.ModelHolder.model = None
            load_models.ModelHolder.model_path = None
        except Exception:
            pass
        import gc

        gc.collect()
