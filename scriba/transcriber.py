"""Transcrição local com faster-whisper (GPU com fallback automático para CPU)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import util
from .config import Whisper

log = logging.getLogger("scriba.transcriber")


@dataclass
class Segment:
    start: float
    end: float
    text: str


class Transcriber:
    def __init__(self, cfg: Whisper, force_cpu: bool = False):
        self.cfg = cfg
        self.force_cpu = force_cpu
        self.model = None
        self.batched = None
        self.device_used: str | None = None

    def ensure_loaded(self) -> str:
        """Carrega o modelo (uma vez) e retorna o device usado ('cuda' ou 'cpu')."""
        if self.model is not None:
            return self.device_used or "cpu"
        util.bootstrap_cuda_dlls()
        from faster_whisper import WhisperModel

        if not self.force_cpu and self.cfg.device in ("auto", "cuda"):
            try:
                import ctranslate2

                if ctranslate2.get_cuda_device_count() > 0:
                    self.model = WhisperModel(self.cfg.model, device="cuda", compute_type="float16")
                    self.device_used = "cuda"
                    return self.device_used
            except Exception:
                pass  # qualquer falha de CUDA cai para CPU
        self.model = WhisperModel(self.cfg.model, device="cpu", compute_type="int8")
        self.device_used = "cpu"
        return self.device_used

    def transcribe(self, wav: Path, on_progress: Callable[[float], None] | None = None) -> list[Segment]:
        self.ensure_loaded()
        try:
            return self._run(wav, on_progress)
        except Exception as e:
            if self.device_used != "cuda":
                raise
            # GPU falhou em runtime — refaz em CPU, mas deixa o motivo visível
            log.warning("GPU falhou em runtime (%s); refazendo em CPU int8", e)
            print(f"AVISO: GPU falhou ({e}); usando CPU")
            from faster_whisper import WhisperModel

            self.model = WhisperModel(self.cfg.model, device="cpu", compute_type="int8")
            self.batched = None
            self.device_used = "cpu"
            return self._run(wav, on_progress)

    def _run(self, wav: Path, on_progress: Callable[[float], None] | None) -> list[Segment]:
        batch = int(self.cfg.batch_size or 0)
        if batch > 1:
            segments = self._run_batched(wav, batch)
            if segments is not None:
                return self._collect(segments, on_progress)
        segments, _info = self.model.transcribe(
            str(wav),
            language=self.cfg.language or None,
            vad_filter=True,
            condition_on_previous_text=False,
            hotwords=self.cfg.hotwords or None,
        )
        return self._collect(segments, on_progress)

    def _run_batched(self, wav: Path, batch: int):
        """Inferência em lote (~2x mais rápida na GPU). None => cair para o modo normal."""
        try:
            if self.batched is None:
                from faster_whisper import BatchedInferencePipeline

                self.batched = BatchedInferencePipeline(self.model)
            kwargs = dict(language=self.cfg.language or None, batch_size=batch)
            if self.cfg.hotwords:
                try:
                    segments, _info = self.batched.transcribe(str(wav), hotwords=self.cfg.hotwords, **kwargs)
                    return segments
                except TypeError:
                    pass  # versão sem suporte a hotwords no modo batched
            segments, _info = self.batched.transcribe(str(wav), **kwargs)
            return segments
        except Exception as e:
            log.warning("modo batched indisponível (%s); usando modo normal", e)
            return None

    @staticmethod
    def _collect(segments, on_progress: Callable[[float], None] | None) -> list[Segment]:
        out: list[Segment] = []
        for s in segments:  # generator: a transcrição acontece aqui
            text = s.text.strip()
            if text:
                out.append(Segment(start=float(s.start), end=float(s.end), text=text))
            if on_progress:
                on_progress(float(s.end))
        return out

    def close(self) -> None:
        """Libera o modelo (e a VRAM) entre reuniões."""
        self.model = None


# Nome do provider conforme #13: a classe acima já satisfaz TranscriptionProvider
# (ver scriba/transcription.py). Alias para uso futuro/explícito, sem churn de imports.
FasterWhisperProvider = Transcriber
