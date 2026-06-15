"""Gravação dual: microfone + loopback WASAPI, em WAVs que sobrevivem a crash."""

from __future__ import annotations

import json
import queue
import struct
import threading
import time
from datetime import datetime
from pathlib import Path

from . import util
from .config import Config

_HEADER_PATCH_INTERVAL = 5.0  # s
_SILENCE_THRESHOLD = 0.5  # s atrás do relógio antes de injetar silêncio


class CrashSafeWav:
    """WAV PCM 16-bit cujo header é re-escrito periodicamente.

    Um kill no meio da gravação perde no máximo os últimos ~5 s de
    consistência do header — o áudio em si fica todo no disco.
    """

    def __init__(self, path: Path, rate: int, channels: int):
        self.path = path
        self.rate = rate
        self.channels = channels
        self.frames_written = 0
        self._f = open(path, "wb")
        self._write_header()
        self._last_patch = time.monotonic()

    def _write_header(self) -> None:
        data_size = self.frames_written * self.channels * 2
        self._f.write(
            struct.pack(
                "<4sI4s4sIHHIIHH4sI",
                b"RIFF", 36 + data_size, b"WAVE",
                b"fmt ", 16, 1, self.channels, self.rate,
                self.rate * self.channels * 2, self.channels * 2, 16,
                b"data", data_size,
            )
        )

    def write(self, chunk: bytes) -> None:
        self._f.write(chunk)
        self.frames_written += len(chunk) // (self.channels * 2)
        now = time.monotonic()
        if now - self._last_patch >= _HEADER_PATCH_INTERVAL:
            self._last_patch = now
            self.patch_header()

    def patch_header(self) -> None:
        self._f.flush()
        data_size = self.frames_written * self.channels * 2
        pos = self._f.tell()
        self._f.seek(4)
        self._f.write(struct.pack("<I", 36 + data_size))
        self._f.seek(40)
        self._f.write(struct.pack("<I", data_size))
        self._f.seek(pos)

    def close(self) -> None:
        self.patch_header()
        self._f.close()


class _StreamRecorder:
    """Um stream PortAudio → fila → thread escritora.

    Com pad_silence=True (loopback), injeta zeros quando o WASAPI fica sem
    entregar dados (nada tocando), mantendo a linha do tempo alinhada.
    """

    def __init__(self, pa, name: str, path: Path, device_info: dict, t0: float, pad_silence: bool):
        import pyaudiowpatch as pyaudio

        self.name = name
        self.rate = int(device_info["defaultSampleRate"])
        self.channels = max(1, min(2, int(device_info["maxInputChannels"])))
        self.device_name = str(device_info["name"])
        self.t0 = t0
        self.pad_silence = pad_silence
        self._q: queue.Queue[bytes | None] = queue.Queue()
        self.wav = CrashSafeWav(path, self.rate, self.channels)
        self.stream = pa.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=int(device_info["index"]),
            frames_per_buffer=1024,
            stream_callback=self._callback,
        )
        self.offset_seconds = time.monotonic() - t0
        self._writer = threading.Thread(target=self._writer_loop, daemon=True, name=f"writer-{name}")
        self._writer.start()

    def _callback(self, in_data, frame_count, time_info, status):
        import pyaudiowpatch as pyaudio

        self._q.put(bytes(in_data))
        return (None, pyaudio.paContinue)

    def _pad_if_behind(self) -> None:
        expected = int((time.monotonic() - self.t0) * self.rate)
        behind = expected - self.wav.frames_written
        if behind > self.rate * _SILENCE_THRESHOLD:
            self.wav.write(b"\x00" * (behind * self.channels * 2))

    def _writer_loop(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                # sem dados (ex.: loopback durante silêncio) — mantém a linha do tempo
                if self.pad_silence:
                    self._pad_if_behind()
                continue
            if item is None:
                return
            if self.pad_silence:
                self._pad_if_behind()
            self.wav.write(item)

    def stop(self) -> None:
        try:
            self.stream.stop_stream()
            self.stream.close()
        except Exception:
            pass
        self._q.put(None)
        self._writer.join(timeout=10)
        self.wav.close()


class Recording:
    """Uma gravação de reunião: mic + loopback numa pasta com meta.json."""

    def __init__(self, cfg: Config):
        import pyaudiowpatch as pyaudio

        util.ensure_app_dirs()
        self.cfg = cfg.audio
        self.base_dir = cfg.output.resolved_recordings_dir()  # cria se não existir
        self.started_at = datetime.now()
        self.folder = self._new_folder()
        self.pa = pyaudio.PyAudio()
        try:
            mic_info = self.pa.get_default_input_device_info()
            lb_info = self._pick_loopback(self.pa)
            self.t0 = time.monotonic()
            self.mic = _StreamRecorder(self.pa, "mic", self.folder / "mic.wav", mic_info, self.t0, pad_silence=False)
            self.loopback = _StreamRecorder(
                self.pa, "loopback", self.folder / "loopback.wav", lb_info, self.t0, pad_silence=True
            )
        except Exception:
            self.pa.terminate()
            raise
        try:
            from .detector import capture_meeting_title

            self.meeting_title = capture_meeting_title(cfg.detection)
        except Exception:
            self.meeting_title = ""
        self._write_meta("recording")

    def _new_folder(self) -> Path:
        # árvore ano/mês/dia + pasta HH-MM da gravação (":" não é válido em nome
        # de pasta no Windows). Ex.: gravacoes\2026\06\12\11-55\
        base = self.base_dir / self.started_at.strftime("%Y") / self.started_at.strftime("%m") \
            / self.started_at.strftime("%d") / self.started_at.strftime("%H-%M")
        folder, n = base, 1
        while folder.exists():
            n += 1
            folder = base.with_name(f"{base.name}_{n}")
        folder.mkdir(parents=True)
        return folder

    def _pick_loopback(self, pa) -> dict:
        want = (self.cfg.loopback_device or "").strip().lower()
        if want:
            for d in pa.get_loopback_device_info_generator():
                if want in d["name"].lower():
                    return d
        return pa.get_default_wasapi_loopback()

    def duration_seconds(self) -> float:
        return time.monotonic() - self.t0

    def _write_meta(self, status: str, extra: dict | None = None) -> None:
        meta = {
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "status": status,
            "streams": {
                "mic": self._stream_meta(self.mic),
                "loopback": self._stream_meta(self.loopback),
            },
        }
        if getattr(self, "meeting_title", ""):
            meta["meeting_title"] = self.meeting_title
        if extra:
            meta.update(extra)
        util.atomic_write_text(
            self.folder / "meta.json",
            json.dumps(meta, ensure_ascii=False, indent=2),
        )

    @staticmethod
    def _stream_meta(s: _StreamRecorder) -> dict:
        return {
            "file": s.wav.path.name,
            "device": s.device_name,
            "rate": s.rate,
            "channels": s.channels,
            "offset_seconds": round(s.offset_seconds, 3),
            # duração realmente captada: comparada à duração da call no notas.md
            # para denunciar stream que morreu no meio ("Áudio incompleto")
            "audio_seconds": round(s.wav.frames_written / s.rate, 1),
        }

    def stop(self, status: str = "recorded") -> dict:
        duration = self.duration_seconds()
        self.mic.stop()
        self.loopback.stop()
        self.pa.terminate()
        self._write_meta(
            status,
            {
                "ended_at": datetime.now().isoformat(timespec="seconds"),
                "duration_seconds": round(duration, 1),
            },
        )
        return {"folder": self.folder, "duration_seconds": duration, "status": status}


# ------------------------------------------------------------ pós-crash ------

def repair_wav_header(path: Path) -> float:
    """Corrige os tamanhos RIFF/data pelo tamanho real do arquivo (pós-crash).

    Retorna a duração real em segundos (0 se o arquivo for inválido).
    """
    try:
        size = path.stat().st_size
    except OSError:
        return 0.0
    if size < 44:
        return 0.0
    data_size = size - 44
    with open(path, "r+b") as f:
        f.seek(22)
        channels = struct.unpack("<H", f.read(2))[0]
        f.seek(24)
        rate = struct.unpack("<I", f.read(4))[0]
        f.seek(40)
        declared = struct.unpack("<I", f.read(4))[0]
        if declared != data_size:
            f.seek(4)
            f.write(struct.pack("<I", 36 + data_size))
            f.seek(40)
            f.write(struct.pack("<I", data_size))
    return data_size / (rate * channels * 2) if rate and channels else 0.0


def repair_folder(folder: Path) -> float:
    """Repara todos os WAVs de uma pasta de reunião. Retorna a maior duração."""
    return max((repair_wav_header(w) for w in folder.glob("*.wav")), default=0.0)


# ------------------------------------------------------------- CLI helpers ---

def record_for(seconds: int, show_ui: bool = True) -> int:
    """`scriba record N`: gravação manual (com a pílula flutuante, se habilitada)."""
    from .config import load

    cfg = load()
    rec = Recording(cfg)
    print(f"gravando em {rec.folder}")
    print(f"  mic:      {rec.mic.device_name} ({rec.mic.rate} Hz, {rec.mic.channels}ch)")
    print(f"  loopback: {rec.loopback.device_name} ({rec.loopback.rate} Hz, {rec.loopback.channels}ch)")

    outcome = "ok"
    if show_ui and cfg.ui.overlay:
        from .overlay import run_countdown

        print(f"{seconds}s... (use a pílula para encerrar ou descartar)")
        try:
            outcome = run_countdown(seconds)
        except Exception as e:
            print(f"(pílula indisponível: {e})")
            show_ui = False
    if not (show_ui and cfg.ui.overlay):
        print(f"{seconds}s... (Ctrl+C para parar antes)")
        try:
            time.sleep(seconds)
        except KeyboardInterrupt:
            print("interrompido.")

    info = rec.stop(status="discarded" if outcome == "discarded" else "recorded")
    if outcome == "discarded":
        print(f"gravação descartada ({info['duration_seconds']:.1f}s): {rec.folder}")
    else:
        print(f"ok: {info['duration_seconds']:.1f}s gravados em {rec.folder}")
    return 0


def list_devices() -> int:
    """`scriba devices`: lista mics e loopbacks WASAPI."""
    import pyaudiowpatch as pyaudio

    pa = pyaudio.PyAudio()
    try:
        wasapi_index = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
        try:
            default_mic = pa.get_default_input_device_info()["index"]
        except Exception:
            default_mic = None
        try:
            default_lb = pa.get_default_wasapi_loopback()["index"]
        except Exception:
            default_lb = None

        print("== Microfones (WASAPI) ==")
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if d["hostApi"] != wasapi_index or d.get("isLoopbackDevice"):
                continue
            if int(d["maxInputChannels"]) <= 0:
                continue
            mark = "  *" if d["index"] == default_mic else "   "
            print(f"{mark} {d['name']}")

        print("\n== Loopbacks (saídas capturáveis) ==")
        for d in pa.get_loopback_device_info_generator():
            mark = "  *" if d["index"] == default_lb else "   "
            print(f"{mark} {d['name']}")
        print("\n(* = padrão; use [audio].loopback_device no config para fixar outro)")
    finally:
        pa.terminate()
    return 0
