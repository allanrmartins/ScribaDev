"""Captura no macOS: mic + loopback (process tap) via sounddevice/PortAudio (#104, M3).

Espelha o modelo do recorder Windows (stream callback → fila → thread escritora →
CrashSafeWav), com o mesmo contrato duck-typed que `Recording` consome:
`.wav .rate .channels .device_name .offset_seconds .looks_dead() .reopen() .stop()`.
O writer (~40 linhas) é DUPLICADO do `_StreamRecorder` de propósito — regra de ouro
do port: o código Windows fica intocado; a extração de uma base comum pode vir depois.

O loopback é o aggregate privado do mac_tap (tap global de todo o áudio do sistema);
`[audio] loopback_device` no darwin escolhe o device de SAÍDA usado como clock do
aggregate (o tap continua global). O mic é o input device do sounddevice.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

from . import mac_tap
from .recorder import CrashSafeWav, peak_level

log = logging.getLogger("scriba.recorder_mac")

_SILENCE_THRESHOLD = 0.5  # s atrás do relógio antes de injetar silêncio (igual ao win)
_MIC_STALL_SECONDS = 5.0  # mic sem dados por mais que isso = device caiu (igual ao win)


def _sd():
    import sounddevice

    return sounddevice


def _refresh_devices() -> None:
    """Re-inicializa o PortAudio p/ ele enxergar devices criados DEPOIS do import
    (o aggregate do tap). API semi-privada do sounddevice; validada no spike M2."""
    sd = _sd()
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        log.exception("refresh da lista de devices falhou (seguindo com a lista atual)")


def _info(sd, index: int) -> dict:
    d = dict(sd.query_devices(index))
    d["index"] = index
    return d


class MacAudioEngine:
    """Análogo do PyAudio() no darwin: dono do tap/aggregate do loopback.
    `Recording` guarda em self.pa e chama .terminate() no stop."""

    def __init__(self):
        self._tap: mac_tap.TapHandle | None = None

    def aggregate_info(self) -> dict | None:
        """Info (sounddevice) do aggregate atual, ou None se ele sumiu da lista."""
        if self._tap is None:
            return None
        sd = _sd()
        for i, d in enumerate(sd.query_devices()):
            if d["name"] == self._tap.agg_name and d["max_input_channels"] > 0:
                return _info(sd, i)
        return None

    def ensure_tap(self, loopback_want: str = "") -> dict:
        """Garante tap+aggregate vivos e devolve a info do aggregate. Se o aggregate
        atual morreu (ex.: clock desplugado), recria — com o clock re-resolvido
        (default de AGORA), igual ao reopen do Windows que re-enumera o default."""
        cur = self.aggregate_info()
        if cur is not None:
            return cur
        mac_tap.destroy(self._tap)
        self._tap = None
        clock_uid = mac_tap.find_output_uid(loopback_want)
        self._tap = mac_tap.create_system_tap(clock_device_uid=clock_uid)
        _refresh_devices()
        cur = self.aggregate_info()
        if cur is None:  # nunca visto no spike; defensivo
            raise RuntimeError("aggregate do tap não apareceu na lista do PortAudio")
        return cur

    def terminate(self) -> None:
        mac_tap.destroy(self._tap)
        self._tap = None


# ------------------------------------------------------- seleção de device ---

def pick_mic(audio_cfg, engine: MacAudioEngine) -> dict:
    """Microfone configurado (substring, case-insensitive) ou o input default."""
    sd = _sd()
    want = (audio_cfg.mic_device or "").strip().lower()
    if want:
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and want in d["name"].lower() \
                    and not d["name"].startswith("ScribaTap"):
                return _info(sd, i)
    idx = sd.default.device[0]
    if idx is None or idx < 0:
        raise RuntimeError("nenhum microfone padrão no sistema")
    return _info(sd, idx)


def pick_loopback(audio_cfg, engine: MacAudioEngine) -> dict:
    """Aggregate do tap (cria/recria se preciso). `loopback_device` escolhe o clock."""
    return engine.ensure_tap((audio_cfg.loopback_device or "").strip().lower())


# ----------------------------------------------------------- stream → WAV ----

class MacStreamRecorder:
    """Um RawInputStream (int16) → fila → thread escritora, com o mesmo contrato
    do _StreamRecorder do Windows (inclusive pad_silence p/ alinhar a linha do
    tempo do loopback e o par looks_dead/reopen p/ troca de device no meio)."""

    def __init__(self, name: str, path: Path, device_info: dict, t0: float, pad_silence: bool):
        sd = _sd()
        self.name = name
        self.rate = int(device_info["default_samplerate"])
        self.channels = max(1, min(2, int(device_info["max_input_channels"])))
        self.device_name = str(device_info["name"])
        self.t0 = t0
        self.pad_silence = pad_silence
        self._q: queue.Queue[bytes | None] = queue.Queue()
        self.wav = CrashSafeWav(path, self.rate, self.channels)
        self.stream = sd.RawInputStream(
            device=int(device_info["index"]), channels=self.channels, samplerate=self.rate,
            dtype="int16", blocksize=1024, callback=self._callback,
        )
        self.stream.start()
        self.offset_seconds = time.monotonic() - t0
        self.last_data = time.monotonic()
        self._writer = threading.Thread(target=self._writer_loop, daemon=True, name=f"writer-{name}")
        self._writer.start()

    def _callback(self, in_data, frames, time_info, status) -> None:
        self.last_data = time.monotonic()
        self._q.put(bytes(in_data))

    def looks_dead(self) -> bool:
        """Mesma heurística do Windows: stream inativo — ou (só p/ o mic, que entrega
        continuamente) sem dados por muito tempo. O loopback pode ficar quieto em
        silêncio legítimo, então nele só conta o active."""
        try:
            if not self.stream.active:
                return True
        except Exception:
            return True
        if not self.pad_silence and (time.monotonic() - self.last_data) > _MIC_STALL_SECONDS:
            return True
        return False

    def reopen(self, engine: MacAudioEngine, device_info: dict) -> bool:
        """Reabre num novo device mantendo o MESMO WAV — só se rate/canais baterem
        (senão degrada com graça devolvendo False, como no Windows)."""
        sd = _sd()
        if int(device_info["default_samplerate"]) != self.rate:
            return False
        if max(1, min(2, int(device_info["max_input_channels"]))) != self.channels:
            return False
        try:
            new_stream = sd.RawInputStream(
                device=int(device_info["index"]), channels=self.channels, samplerate=self.rate,
                dtype="int16", blocksize=1024, callback=self._callback,
            )
            new_stream.start()
        except Exception:
            return False
        old, self.stream = self.stream, new_stream
        self.device_name = str(device_info["name"])
        self.last_data = time.monotonic()
        try:
            old.stop()
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
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        self._q.put(None)
        self._writer.join(timeout=10)
        self.wav.close()


# ------------------------------------------------ medidor de nível (teste) ---

class LevelProbeMac:
    """Streams de TESTE (mic + loopback) expondo o pico 0..1 de cada — o botão
    'Testar microfone' das Configurações. Dono do próprio engine/tap."""

    def __init__(self, audio_cfg):
        sd = _sd()
        self._levels = {"mic": 0.0, "loopback": 0.0}
        self._streams: list = []
        self.engine = MacAudioEngine()
        try:
            # loopback primeiro: criar o aggregate re-inicializa o PortAudio e
            # invalidaria um índice de mic escolhido antes
            for name, info in (("loopback", pick_loopback(audio_cfg, self.engine)),
                               ("mic", pick_mic(audio_cfg, self.engine))):
                ch = max(1, min(2, int(info["max_input_channels"])))
                st = sd.RawInputStream(
                    device=int(info["index"]), channels=ch,
                    samplerate=int(info["default_samplerate"]), dtype="int16",
                    blocksize=1024, callback=self._make_cb(name),
                )
                st.start()
                self._streams.append(st)
        except Exception:
            self.stop()
            raise

    def _make_cb(self, name: str):
        def _cb(in_data, frames, time_info, status) -> None:
            self._levels[name] = peak_level(bytes(in_data))

        return _cb

    def level(self, name: str) -> float:
        return self._levels.get(name, 0.0)

    def stop(self) -> None:
        for st in self._streams:
            try:
                st.stop()
                st.close()
            except Exception:
                pass
        self._streams = []
        self.engine.terminate()


# --------------------------------------------------- sonda / CLI (audioprobe) ---

def _output_devices(sd) -> list[tuple[int, str]]:
    """Saídas reais (alvos de clock do tap), sem os aggregates ScribaTap."""
    return [(i, d["name"]) for i, d in enumerate(sd.query_devices())
            if d["max_output_channels"] > 0 and not d["name"].startswith("ScribaTap")]


def probe_defaults() -> dict:
    """audioprobe sem argumento: nomes dos devices padrão (pré-checagem). NÃO cria
    tap nenhum — a sonda roda a cada pré-gravação/health check."""
    sd = _sd()
    mic_idx, out_idx = sd.default.device
    return {
        "mic": str(sd.query_devices(mic_idx)["name"]) if mic_idx is not None and mic_idx >= 0 else "",
        "loopback": str(sd.query_devices(out_idx)["name"]) if out_idx is not None and out_idx >= 0 else "",
    }


def probe_list() -> dict:
    """audioprobe 'list': listas p/ os seletores da UI. No darwin os 'loopbacks'
    são os devices de SAÍDA (alvos de clock do tap)."""
    sd = _sd()
    mics = [d["name"] for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0 and not d["name"].startswith("ScribaTap")]
    defaults = probe_defaults()
    seen: set[str] = set()
    return {
        "mics": [m for m in mics if not (m in seen or seen.add(m))],
        "loopbacks": [n for _, n in _output_devices(sd)],
        "default_mic": defaults["mic"],
        "default_loopback": defaults["loopback"],
    }


def list_devices_text() -> int:
    """`scriba devices` no darwin."""
    sd = _sd()
    defaults = probe_defaults()
    print("== Microfones (CoreAudio) ==")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0 or d["name"].startswith("ScribaTap"):
            continue
        mark = "  *" if d["name"] == defaults["mic"] else "   "
        print(f"{mark} {d['name']}")
    print("\n== Saídas (clock do tap de sistema) ==")
    for _, name in _output_devices(sd):
        mark = "  *" if name == defaults["loopback"] else "   "
        print(f"{mark} {name}")
    print("\n(* = padrão; o tap captura TODO o áudio do sistema — "
          "[audio].loopback_device escolhe só o clock)")
    return 0
