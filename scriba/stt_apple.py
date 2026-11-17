"""Transcrição pelo motor de fala da Apple (SpeechAnalyzer, macOS 26+) - #222.

Provider do seam `transcription.TranscriptionProvider` para engine="apple". A API
da Apple é Swift-only e async, então a transcrição roda num programa Swift pequeno
(`scriba/apple_stt/AppleSTT.swift`) que o Python chama e lê em JSON Lines:

- no app do DMG ele vem compilado dentro do bundle;
- no modo código-fonte ele é compilado na 1ª vez (xcrun swiftc, Command Line Tools)
  e guardado em APP_DIR/bin com o hash do fonte no nome - um `git pull` que mude o
  .swift recompila sozinho.

Transcrição AO VIVO (opcional, whisper.live): `LiveSession` sobe um helper por stream
no início da gravação; ele lê o .wav enquanto o gravador escreve (nunca toca no
gravador: se o helper cair, a gravação segue intacta), grava os trechos fixados em
`<stream>.live.jsonl` e manda a frase em andamento por um pipe (memória). Tudo isso é
só para a TELA (capa): roda em modo de baixa latência (.fastResults), que troca ~3%
das palavras. A nota sempre transcreve o arquivo no modo preciso ao fim da call -
segundos com este motor (27 min em ~6 s) - e sai idêntica à de quem não usa o ao vivo.

Qualquer falha do motor da Apple (Mac antigo, sem Command Line Tools, idioma sem
suporte) cai para o motor Whisper desta máquina - mesmo espírito do fallback do MLX.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from . import util
from .config import Whisper
from .transcriber import Segment

log = logging.getLogger("scriba.stt_apple")

_SOURCE = Path(__file__).resolve().parent / "apple_stt" / "AppleSTT.swift"
LIVE_SUFFIX = ".live.jsonl"


def macos_suportado() -> bool:
    """macOS 26+ (o SpeechAnalyzer estreou no 26)."""
    if sys.platform != "darwin":
        return False
    try:
        return int(platform.mac_ver()[0].split(".")[0]) >= 26
    except (ValueError, IndexError):
        return False


def _bundled_binary() -> Path | None:
    if getattr(sys, "frozen", False):
        p = util.resource_path("apple_stt", "apple-stt")
        return p if p.exists() else None
    return None


def helper_path() -> Path:
    """Caminho do helper pronto p/ rodar; compila no modo código-fonte se preciso.
    Levanta RuntimeError com o motivo se não houver como."""
    if not macos_suportado():
        raise RuntimeError("motor da Apple exige macOS 26 ou mais novo")
    bundled = _bundled_binary()
    if bundled is not None:
        return bundled
    if getattr(sys, "frozen", False):
        raise RuntimeError("este app foi gerado sem o motor da Apple")
    digest = hashlib.sha256(_SOURCE.read_bytes()).hexdigest()[:12]
    out = util.APP_DIR / "bin" / f"apple-stt-{digest}"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")
    try:
        r = subprocess.run(["xcrun", "swiftc", "-O", "-o", str(tmp), str(_SOURCE)],
                           capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"não consegui compilar o motor da Apple ({e}); "
                           "instale as Command Line Tools: xcode-select --install") from e
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("falha ao compilar o motor da Apple: "
                           + (r.stderr.strip().splitlines() or ["?"])[-1][:200])
    os.replace(tmp, out)   # atômico: app e subprocesso podem compilar ao mesmo tempo
    for old in out.parent.glob("apple-stt-*"):
        if old != out and not old.name.endswith(".tmp"):
            old.unlink(missing_ok=True)
    log.info("motor da Apple compilado: %s", out.name)
    return out


def check(language: str) -> tuple[bool, str]:
    """(disponível, detalhe) p/ o idioma - doctor, Configurações e o provider."""
    try:
        r = subprocess.run([str(helper_path()), "check", language or "pt"],
                           capture_output=True, text=True, timeout=60)
        info = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:
        return False, str(e)
    if not info.get("available"):
        return False, info.get("reason", "indisponível")
    extra = "" if info.get("installed") else " (o modelo do idioma baixa na 1ª transcrição)"
    return True, f"{info['locale']}{extra}"


def _parse(lines: list[str]) -> tuple[list[Segment], bool]:
    """Linhas JSONL do helper -> (trechos, terminou?). Linha truncada (helper morto
    no meio da escrita) é ignorada."""
    segs: list[Segment] = []
    done = False
    for line in lines:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("done"):
            done = True
        elif obj.get("text"):
            segs.append(Segment(start=float(obj["start"]), end=float(obj["end"]), text=str(obj["text"])))
    return segs, done


class AppleSpeechProvider:
    """Satisfaz TranscriptionProvider (device_used/ensure_loaded/transcribe/close)."""

    def __init__(self, cfg: Whisper, fallback_factory: Callable[[], object]):
        self.cfg = cfg
        self.device_used: str | None = None
        self._fallback_factory = fallback_factory
        self._fallback = None
        self._helper: Path | None = None

    def _use_fallback(self, motivo: str) -> None:
        log.warning("motor da Apple indisponível (%s); usando o Whisper", motivo)
        print(f"AVISO: motor da Apple indisponível ({motivo}); usando o Whisper")
        self._fallback = self._fallback_factory()
        self.device_used = self._fallback.ensure_loaded()

    def ensure_loaded(self) -> str:
        if self._fallback is not None:
            return self._fallback.ensure_loaded()
        ok, detalhe = check(self.cfg.language)
        if not ok:
            self._use_fallback(detalhe)
            return self.device_used
        self._helper = helper_path()
        self.device_used = "apple"
        return self.device_used

    def transcribe(self, wav: Path, on_progress: Callable[[float], None] | None = None) -> list[Segment]:
        wav = Path(wav)
        if self._fallback is None and self._helper is None:
            self.ensure_loaded()
        if self._fallback is not None:
            return self._fallback.transcribe(wav, on_progress)
        # sempre o arquivo, no modo preciso: o .live.jsonl (baixa latência) é só da tela
        try:
            out = self._transcribe_file(wav)
        except Exception as e:
            self._use_fallback(str(e))
            return self._fallback.transcribe(wav, on_progress)
        if on_progress:
            for s in out:
                on_progress(s.end)
        return out

    def _transcribe_file(self, wav: Path) -> list[Segment]:
        with tempfile.TemporaryDirectory(prefix="scriba_apple_") as tmp:
            src = wav
            if wav.suffix.lower() != ".wav":
                # áudio arquivado (opus/flac): o AVAudioFile não lê opus - decodifica
                ff = util.ffmpeg_command()
                if not ff:
                    raise RuntimeError(f"ffmpeg ausente p/ decodificar {wav.name}")
                src = Path(tmp) / "audio.wav"
                subprocess.run([*ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
                                "-ac", "1", "-ar", "16000", str(src)], check=True, timeout=600)
            r = subprocess.run([str(self._helper), "file", self.cfg.language or "pt", str(src)],
                               capture_output=True, text=True, timeout=3600)
        segs, done = _parse(r.stdout.splitlines())
        if r.returncode != 0 or not done:
            raise RuntimeError((r.stderr.strip().splitlines() or [f"saída {r.returncode}"])[-1][:200])
        return segs

    def close(self) -> None:
        if self._fallback is not None:
            self._fallback.close()


class LiveSession:
    """Transcrição ao vivo de uma gravação: um helper por stream (mic e loopback).

    Fail-safe por desenho: tudo aqui é best-effort e nada levanta para quem chama -
    o pior caso é o pipeline transcrever o arquivo ao fim, como sem o ao vivo. Sobe
    numa thread própria: compilar o helper na 1ª vez não pode atrasar a gravação."""

    STREAMS = ("mic", "loopback")

    def __init__(self, folder: Path, language: str):
        import threading

        self.folder = Path(folder)
        self.language = language or "pt"
        self._procs: list[subprocess.Popen] = []
        self._lock = threading.Lock()
        self._ended: str | None = None   # "stop" | "kill" pedido antes de subir
        # texto PROVISÓRIO (a frase ainda sendo dita) por stream, só em memória: chega por
        # um pipe do helper e a capa lê daqui (#capa ao vivo). Nunca vai para o disco.
        self._partials: dict[str, tuple[float, str]] = {}
        threading.Thread(target=self._launch, daemon=True, name="apple-live").start()

    def partials(self) -> dict[str, tuple[float, str]]:
        """{stream: (início em s, texto provisório)} do que está sendo dito agora."""
        with self._lock:
            return dict(self._partials)

    def _read_partials(self, name: str, fd: int) -> None:
        try:
            with os.fdopen(fd, "rb") as f:
                for raw in f:
                    try:
                        obj = json.loads(raw)
                        start, text = float(obj.get("start", 0.0)), str(obj.get("text", "")).strip()
                    except (ValueError, TypeError, AttributeError):
                        continue
                    with self._lock:
                        if text:
                            self._partials[name] = (start, text)
                        else:
                            self._partials.pop(name, None)   # o trecho fixou
        except Exception as e:
            log.debug("canal do provisório de %s fechou: %s", name, e)
        finally:
            with self._lock:
                self._partials.pop(name, None)

    def _launch(self) -> None:
        import threading

        try:
            helper = helper_path()
        except Exception as e:
            log.warning("transcrição ao vivo indisponível: %s", e)
            return
        for name in self.STREAMS:
            with self._lock:
                if self._ended:
                    break
                r = w = None
                try:
                    r, w = os.pipe()   # canal do provisório: o helper escreve no w herdado
                    with open(self.folder / f"{name}{LIVE_SUFFIX}", "w", encoding="utf-8") as out, \
                            open(self.folder / f"{name}.live.log", "w", encoding="utf-8") as err:
                        self._procs.append(subprocess.Popen(
                            [str(helper), "live", self.language, str(self.folder / f"{name}.wav"), str(w)],
                            stdin=subprocess.PIPE, stdout=out, stderr=err, pass_fds=(w,)))
                    os.close(w)
                    w = None
                    threading.Thread(target=self._read_partials, args=(name, r), daemon=True,
                                     name=f"apple-live-{name}").start()
                    r = None
                except Exception as e:
                    log.warning("transcrição ao vivo de %s não subiu: %s", name, e)
                finally:
                    for fd in (r, w):
                        if fd is not None:
                            os.close(fd)
        with self._lock:
            if self._procs:
                log.info("transcrição ao vivo ligada: %s", self.folder.name)
            ended = self._ended
        if ended:
            self._end(ended)

    def _end(self, how: str) -> None:
        for p in self._procs:
            try:
                p.kill() if how == "kill" else p.stdin.close()
            except Exception:
                pass

    def stop(self) -> None:
        """Gravação parou: fecha o stdin (o helper finaliza o que ouviu e sai sozinho)."""
        with self._lock:
            self._ended = "stop"
            procs = list(self._procs)
        if procs:
            self._end("stop")

    def kill(self) -> None:
        """Gravação descartada: encerra já, sem finalizar."""
        with self._lock:
            self._ended = "kill"
        self._end("kill")
