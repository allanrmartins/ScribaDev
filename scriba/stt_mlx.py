"""Transcrição local acelerada por Metal (Apple Silicon) via mlx-whisper (#104, M5).

Provider do seam `transcription.TranscriptionProvider`, escolhido pela fábrica
`make_transcriber` quando engine=local roda num mac arm64 com mlx_whisper
instalado (ou engine="mlx" explícito). Mapeia o nome de modelo do faster-whisper
("large-v3-turbo") para o repo MLX equivalente ("mlx-community/whisper-large-v3-turbo");
um repo explícito (com "/") passa direto.

Diferenças vs faster-whisper aceitas no plano:
- sem parâmetro `hotwords` → o vocabulário vai como `initial_prompt` (validado no
  spike M2 com a fixture pt-BR);
- sem VAD Silero embutido → o recorte de fala é feito AQUI, antes do modelo
  (`vadcut`, #202): sem ele, o stream do microfone numa call em que a pessoa mais
  escuta do que fala (17 min de silêncio em 19,5) virava texto inventado que
  seguia para a ata e para o resumo. Os tempos voltam ao relógio original antes
  de sair daqui, então merge e diarização não sabem do recorte. `vad_filter =
  false` no config desliga (áudio inteiro ao modelo, só p/ depurar).

Falha em runtime (repo inexistente, MLX quebrado, OOM) cai para o faster-whisper
em CPU — espelho do fallback CUDA→CPU do transcriber (transcriber.py:54-70).
Falha no recorte (VAD indisponível, áudio que não decodifica) NÃO derruba nada:
transcreve o arquivo inteiro como antes, com aviso no log.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from .config import Whisper
from .transcriber import Segment, Transcriber, vad_enabled, vad_parameters

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

    def _speech_only(self, wav: Path):
        """(áudio a entregar ao modelo, trechos de fala em amostras | None).

        Com o filtro ligado: decodifica, detecta a fala com o Silero e devolve só os
        trechos com voz emendados + os trechos p/ remapear os tempos depois. Sem
        fala nenhuma: (None, []) — não há o que transcrever, e é exatamente o caso
        em que o modelo inventaria texto. Filtro desligado ou qualquer falha no
        recorte: (caminho do arquivo, None) — o comportamento de antes."""
        if not vad_enabled(self.cfg):
            return str(wav), None
        try:
            from . import vadcut

            audio = vadcut.load_audio(wav)
            chunks = vadcut.detect_speech(audio, params=vad_parameters(self.cfg))
            print(f"  {vadcut.describe(chunks, len(audio))}")
            if not chunks:
                return None, []
            return vadcut.concat_speech(audio, chunks), chunks
        except Exception as e:
            log.warning("recorte de fala (VAD) indisponível (%s); transcrevendo o áudio inteiro no MLX", e)
            print(f"AVISO: filtro de voz indisponível ({e}); áudio inteiro ao modelo")
            return str(wav), None

    def transcribe(self, wav: Path, on_progress: Callable[[float], None] | None = None) -> list[Segment]:
        if self._fallback is not None:
            return self._fallback.transcribe(wav, on_progress)
        try:
            self.ensure_loaded()
            import mlx_whisper

            audio_in, chunks = self._speech_only(wav)
            if audio_in is None:
                log.info("%s: nenhum trecho com fala; nada a transcrever", Path(wav).name)
                return []
            # mlx_whisper aceita o caminho OU o waveform (float32 16 kHz) direto
            result = mlx_whisper.transcribe(
                audio_in,
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
        if chunks:
            # o modelo viu o áudio recortado: devolve os tempos ao relógio do stream
            from . import vadcut

            out = vadcut.restore_segments(out, chunks)
        if on_progress:
            for s in out:
                on_progress(s.end)
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
