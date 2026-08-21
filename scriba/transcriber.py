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

    def _abrir_modelo(self, device: str, compute_type: str, **kw):
        """Abre o WhisperModel preferindo o CACHE LOCAL, com a rede só como plano B.

        faster-whisper resolve o nome do modelo pelo Hugging Face Hub a CADA carga -
        mesmo com o modelo inteiro em cache, isso é uma ida à rede antes de qualquer
        transcrição. Numa rede que engasga (proxy corporativo, portal cativo, DNS
        preso) essa chamada fica pendurada sem timeout: a transcrição nunca começa,
        o processo não consome CPU nem GPU e o process.log para na última linha
        (#176). Com o modelo já baixado, nada disso precisa acontecer.
        """
        from faster_whisper import WhisperModel

        try:
            return WhisperModel(self.cfg.model, device=device, compute_type=compute_type,
                                local_files_only=True, **kw)
        except Exception as e:
            log.info("modelo %s não resolvido no cache local (%s) - buscando no Hugging Face",
                     self.cfg.model, e)
            return WhisperModel(self.cfg.model, device=device, compute_type=compute_type, **kw)

    def ensure_loaded(self) -> str:
        """Carrega o modelo (uma vez) e retorna o device usado ('cuda' ou 'cpu')."""
        if self.model is not None:
            return self.device_used or "cpu"
        util.bootstrap_cuda_dlls()
        if not self.force_cpu and self.cfg.device in ("auto", "cuda"):
            try:
                import ctranslate2

                if ctranslate2.get_cuda_device_count() > 0:
                    self.model = self._abrir_modelo("cuda", "float16")
                    self.device_used = "cuda"
                    self._warmup()  # paga o JIT/autotune do CUDA fora do relógio percebido (#6)
                    return self.device_used
            except Exception:
                pass  # qualquer falha de CUDA cai para CPU
        self.model = self._abrir_modelo("cpu", "int8",
                                        cpu_threads=int(self.cfg.cpu_threads or 0))
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
            self.model = self._abrir_modelo("cpu", "int8",
                                            cpu_threads=int(self.cfg.cpu_threads or 0))
            self.batched = None
            self.device_used = "cpu"
            return self._run(wav, on_progress)

    def _run(self, wav: Path, on_progress: Callable[[float], None] | None) -> list[Segment]:
        beam = max(1, int(self.cfg.beam_size or 3))
        vad_params = self._vad_parameters()
        batch = int(self.cfg.batch_size or 0)
        if batch > 1:
            segments = self._run_batched(wav, batch, beam, vad_params)
            if segments is not None:
                return self._collect(segments, on_progress)
        kwargs = dict(
            language=self.cfg.language or None,
            vad_filter=True,
            condition_on_previous_text=False,
            hotwords=self.cfg.hotwords or None,
            beam_size=beam,
        )
        if vad_params:
            kwargs["vad_parameters"] = vad_params
        segments, _info = self.model.transcribe(str(wav), **kwargs)
        return self._collect(segments, on_progress)

    def _run_batched(self, wav: Path, batch: int, beam: int, vad_params: dict | None):
        """Inferência em lote (~2x mais rápida na GPU). None => cair para o modo normal."""
        try:
            if self.batched is None:
                from faster_whisper import BatchedInferencePipeline

                self.batched = BatchedInferencePipeline(self.model)
            kwargs = dict(language=self.cfg.language or None, batch_size=batch, beam_size=beam)
            if vad_params:
                kwargs["vad_parameters"] = vad_params
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

    def _vad_parameters(self) -> dict | None:
        """Parâmetros do VAD (opt-in, #6). Tudo zero => None: mantém o default da lib
        (nenhuma mudança de comportamento até o usuário calibrar com áudio real)."""
        params: dict = {}
        ms = int(getattr(self.cfg, "vad_min_silence_ms", 0) or 0)
        if ms > 0:
            params["min_silence_duration_ms"] = ms
        th = float(getattr(self.cfg, "vad_threshold", 0.0) or 0.0)
        if th > 0:
            params["threshold"] = th
        return params or None

    def _warmup(self) -> None:
        """Inferência muda de ~1 s de silêncio logo após carregar na GPU: paga o
        JIT/autotune do CUDA fora do relógio percebido (#6). Só na GPU; nunca quebra
        a transcrição (isolada em try/except)."""
        if self.device_used != "cuda" or self.model is None:
            return
        try:
            import time

            import numpy as np

            t0 = time.monotonic()
            silence = np.zeros(16000, dtype=np.float32)  # 1 s a 16 kHz mono
            segments, _info = self.model.transcribe(
                silence, beam_size=1, vad_filter=False, language=self.cfg.language or None
            )
            for _ in segments:  # generator preguiçoso: a inferência só roda ao drenar
                pass
            log.debug("warm-up CUDA concluído em %.2fs", time.monotonic() - t0)
        except Exception as e:
            log.debug("warm-up pulado (%s)", e)

    def close(self) -> None:
        """Libera o modelo (e a VRAM) entre estágios/reuniões."""
        self.model = None
        self.batched = None  # BatchedInferencePipeline segura o modelo: sem isto o GC não libera
        import gc

        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


# Nome do provider conforme #13: a classe acima já satisfaz TranscriptionProvider
# (ver scriba/transcription.py). Alias para uso futuro/explícito, sem churn de imports.
FasterWhisperProvider = Transcriber
