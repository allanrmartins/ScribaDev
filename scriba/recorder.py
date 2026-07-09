"""Gravação dual: microfone + loopback WASAPI, em WAVs que sobrevivem a crash."""

from __future__ import annotations

import json
import logging
import queue
import struct
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import util
from .config import Config

log = logging.getLogger("scriba.recorder")

# captura ao vivo = WASAPI loopback (pyaudiowpatch), Windows-only por ora; a
# captura POSIX (PipeWire/PulseAudio, CoreAudio) vem em marcos futuros (#104)
_CAPTURE_UNSUPPORTED_MSG = "captura de áudio ao vivo não suportada neste SO ainda (Windows-only por ora)"

_HEADER_PATCH_INTERVAL = 5.0  # s
_SILENCE_THRESHOLD = 0.5  # s atrás do relógio antes de injetar silêncio
_MIC_STALL_SECONDS = 5.0  # mic sem entregar dados por mais que isso = device caiu (#22)
_DEVICE_POLL_SECONDS = 2.0  # ronda do watcher de troca de dispositivo


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
        self.last_data = time.monotonic()  # p/ detectar stream que parou (device caiu)
        self._writer = threading.Thread(target=self._writer_loop, daemon=True, name=f"writer-{name}")
        self._writer.start()

    def _callback(self, in_data, frame_count, time_info, status):
        import pyaudiowpatch as pyaudio

        self.last_data = time.monotonic()
        self._q.put(bytes(in_data))
        return (None, pyaudio.paContinue)

    def looks_dead(self) -> bool:
        """Heurística de 'o device sumiu': o stream não está mais ativo — ou (só p/ o
        mic, que entrega buffers continuamente) ficou sem dados por muito tempo. O
        loopback fica quieto em silêncio legítimo, então só conta o is_active dele."""
        try:
            if not self.stream.is_active():
                return True
        except Exception:
            return True
        if not self.pad_silence and (time.monotonic() - self.last_data) > _MIC_STALL_SECONDS:
            return True
        return False

    def reopen(self, pa, device_info: dict) -> bool:
        """Reabre o stream num novo device mantendo o MESMO WAV e thread escritora — só
        se rate e canais baterem com o WAV já aberto (senão a linha do tempo corromperia;
        nesse caso retorna False e mantém o stream antigo, degradando com graça)."""
        import pyaudiowpatch as pyaudio

        if int(device_info["defaultSampleRate"]) != self.rate:
            return False
        if max(1, min(2, int(device_info["maxInputChannels"]))) != self.channels:
            return False
        try:
            new_stream = pa.open(
                format=pyaudio.paInt16, channels=self.channels, rate=self.rate, input=True,
                input_device_index=int(device_info["index"]), frames_per_buffer=1024,
                stream_callback=self._callback,
            )
        except Exception:
            return False
        old, self.stream = self.stream, new_stream
        self.device_name = str(device_info["name"])
        self.last_data = time.monotonic()
        try:
            old.stop_stream()
            old.close()
        except Exception:
            pass
        return True

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


# ------------------------------------------------------- seleção de device ---

def _pick_mic(audio_cfg, pa) -> dict:
    """Microfone configurado (substring do nome, case-insensitive) ou o padrão do
    Windows se vazio/não encontrado (ex.: headset desplugado entre calls)."""
    want = (audio_cfg.mic_device or "").strip().lower()
    if want:
        import pyaudiowpatch as pyaudio

        try:
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
            for i in range(pa.get_device_count()):
                d = pa.get_device_info_by_index(i)
                if (d["hostApi"] == wasapi and not d.get("isLoopbackDevice")
                        and int(d["maxInputChannels"]) > 0 and want in d["name"].lower()):
                    return d
        except Exception:
            pass  # device sumiu / enumeração falhou -> cai no padrão
    return pa.get_default_input_device_info()


def _pick_loopback(audio_cfg, pa) -> dict:
    """Saída a capturar (substring do nome) ou o loopback padrão do Windows."""
    want = (audio_cfg.loopback_device or "").strip().lower()
    if want:
        for d in pa.get_loopback_device_info_generator():
            if want in d["name"].lower():
                return d
    return pa.get_default_wasapi_loopback()


# ------------------------------------------------ medidor de nível (teste) ---

def peak_level(pcm: bytes) -> float:
    """Pico de um buffer PCM 16-bit, normalizado em 0..1. Sem numpy/audioop (usa o
    `array` da stdlib). Pico (não RMS): o objetivo é só responder 'está entrando som?'."""
    import array

    if not pcm:
        return 0.0
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])  # alinha em múltiplo de 2 bytes (int16)
    return max((abs(x) for x in a), default=0) / 32768.0


class LevelProbe:
    """Abre streams de TESTE do mic e do loopback escolhidos e expõe o pico (0..1) de
    cada um — para o botão 'Testar microfone' das Configurações. Não grava nada.

    Roda no processo principal, então o chamador DEVE rodar util.run_audio_probe()
    antes (igual ao start_recording) para o PortAudio não abortar com assert de CRT.
    """

    def __init__(self, audio_cfg):
        import pyaudiowpatch as pyaudio

        self._levels = {"mic": 0.0, "loopback": 0.0}
        self._streams: list = []
        self.pa = pyaudio.PyAudio()
        try:
            for name, info in (("mic", _pick_mic(audio_cfg, self.pa)),
                               ("loopback", _pick_loopback(audio_cfg, self.pa))):
                ch = max(1, min(2, int(info["maxInputChannels"])))
                self._streams.append(self.pa.open(
                    format=pyaudio.paInt16, channels=ch, rate=int(info["defaultSampleRate"]),
                    input=True, input_device_index=int(info["index"]),
                    frames_per_buffer=1024, stream_callback=self._make_cb(name),
                ))
        except Exception:
            self.stop()
            raise

    def _make_cb(self, name: str):
        import pyaudiowpatch as pyaudio

        def _cb(in_data, frame_count, time_info, status):
            self._levels[name] = peak_level(bytes(in_data))
            return (None, pyaudio.paContinue)

        return _cb

    def level(self, name: str) -> float:
        return self._levels.get(name, 0.0)

    def stop(self) -> None:
        for st in self._streams:
            try:
                st.stop_stream()
                st.close()
            except Exception:
                pass
        self._streams = []
        try:
            self.pa.terminate()
        except Exception:
            pass


class Recording:
    """Uma gravação de reunião: mic + loopback numa pasta com meta.json."""

    def __init__(self, cfg: Config):
        if sys.platform != "win32":
            # protege também o caminho da GUI (start_recording): erro claro em
            # vez de ModuleNotFoundError de pyaudiowpatch
            raise RuntimeError(_CAPTURE_UNSUPPORTED_MSG)
        import pyaudiowpatch as pyaudio

        util.ensure_app_dirs()
        self.cfg = cfg.audio
        self.base_dir = cfg.output.resolved_recordings_dir()  # cria se não existir
        self.started_at = datetime.now()
        self.folder = self._new_folder()
        self.pa = pyaudio.PyAudio()
        try:
            mic_info = _pick_mic(self.cfg, self.pa)
            lb_info = _pick_loopback(self.cfg, self.pa)
            self.t0 = time.monotonic()
            self.mic = _StreamRecorder(self.pa, "mic", self.folder / "mic.wav", mic_info, self.t0, pad_silence=False)
            self.loopback = _StreamRecorder(
                self.pa, "loopback", self.folder / "loopback.wav", lb_info, self.t0, pad_silence=True
            )
            # watcher de troca de dispositivo (#22): reabre streams que caírem no meio da call
            self._switches: list = []
            self._stop_watch = threading.Event()
            self._watcher = threading.Thread(target=self._device_watch, daemon=True, name="devwatch")
            self._watcher.start()
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

    def duration_seconds(self) -> float:
        return time.monotonic() - self.t0

    def _device_watch(self) -> None:
        """Ronda em thread: reabre streams cujo device caiu (ex.: fone desplugado no
        meio da call). TUDO defensivo — nunca derruba a gravação."""
        while not self._stop_watch.wait(_DEVICE_POLL_SECONDS):
            try:
                self._check_devices()
            except Exception:
                log.exception("watcher de dispositivo falhou (ignorado)")

    def _check_devices(self) -> None:
        dead = [(n, s) for n, s in (("mic", self.mic), ("loopback", self.loopback)) if s.looks_dead()]
        if not dead:
            return
        # só enumera no processo principal DEPOIS que a sonda (subprocesso) confirma que o
        # PortAudio não vai abortar com assert de CRT durante a troca de dispositivo
        if util.run_audio_probe() is None:
            return  # ainda instável — tenta na próxima ronda
        pickers = {"mic": _pick_mic, "loopback": _pick_loopback}
        for name, s in dead:
            try:
                info = pickers[name](self.cfg, self.pa)
                if s.reopen(self.pa, info):
                    self._switches.append({
                        "stream": name,
                        "device": str(info["name"]),
                        "at_seconds": round(self.duration_seconds(), 1),
                    })
                    log.info("dispositivo reaberto no meio da call: %s -> %s", name, info["name"])
            except Exception:
                log.exception("falha ao reabrir o stream %s", name)

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
        if getattr(self, "_switches", None):
            meta["device_switches"] = self._switches  # trocas de dispositivo no meio (#22)
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
        if getattr(self, "_stop_watch", None) is not None:
            self._stop_watch.set()  # encerra o watcher antes de fechar os streams
            self._watcher.join(timeout=1)
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
    if sys.platform != "win32":
        print(_CAPTURE_UNSUPPORTED_MSG)
        return 1
    from .config import load

    cfg = load()
    rec = Recording(cfg)
    print(f"gravando em {rec.folder}")
    print(f"  mic:      {rec.mic.device_name} ({rec.mic.rate} Hz, {rec.mic.channels}ch)")
    print(f"  loopback: {rec.loopback.device_name} ({rec.loopback.rate} Hz, {rec.loopback.channels}ch)")

    outcome = "ok"
    if show_ui and cfg.ui.overlay:
        from .qt.overlay import run_countdown

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
    if sys.platform != "win32":
        print(_CAPTURE_UNSUPPORTED_MSG)
        return 1
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
