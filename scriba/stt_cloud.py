"""Provider de transcrição na NUVEM (endpoint OpenAI-compatível /audio/transcriptions).

Groq (`whisper-large-v3-turbo`) é o alvo; o mesmo provider serve OpenAI. HTTP +
multipart via stdlib (`urllib`) — sem dependência nova. O áudio é transcodado para
opus 16 kHz mono e **fatiado** em trechos de `_CHUNK_SECONDS` antes do upload (o
endpoint falha SILENCIOSAMENTE acima de ~30 MB). Falha irrecuperável => `raise`, e o
pipeline marca a pasta como `failed` (sem fallback silencioso para o local). (#23)

Privacidade: este provider ENVIA o áudio para fora da máquina — é opt-in explícito
(o usuário escolhe engine=cloud nas Configurações). A diarização segue lendo o WAV
local; só os trechos transcodados sobem.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from . import util
from .config import Whisper
from .transcriber import Segment

_GROQ_DEFAULT = "https://api.groq.com/openai/v1"
_CHUNK_SECONDS = 600           # ~10 min/trecho => ~1,8 MB em opus 24k (folga ao limite de 30 MB)
_HTTP_TIMEOUT = 300
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _http_multipart(url, fields, file_field, file_name, file_bytes, content_type,
                    *, headers=None, timeout) -> dict | None:
    """POST multipart/form-data (stdlib) e devolve o JSON da resposta, ou None (com
    diagnóstico) em falha."""
    boundary = "----scriba" + uuid.uuid4().hex
    crlf = b"\r\n"
    buf = b""
    for name, value in fields.items():
        buf += b"--" + boundary.encode() + crlf
        buf += b'Content-Disposition: form-data; name="' + name.encode() + b'"' + crlf + crlf
        buf += str(value).encode("utf-8") + crlf
    buf += b"--" + boundary.encode() + crlf
    buf += (b'Content-Disposition: form-data; name="' + file_field.encode()
            + b'"; filename="' + file_name.encode() + b'"' + crlf)
    buf += b"Content-Type: " + content_type.encode() + crlf + crlf
    buf += file_bytes + crlf
    buf += b"--" + boundary.encode() + b"--" + crlf

    req = urllib.request.Request(url, data=buf, method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = " " + e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        print(f"STT nuvem: HTTP {e.code} em {url}{detail}")
        return None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        print(f"STT nuvem: falha de conexão em {url} ({e})")
        return None


def _chunk_to_opus(wav: Path, out_dir: Path) -> list[Path]:
    """Transcoda o WAV para opus 16 kHz mono e fatia em trechos de _CHUNK_SECONDS,
    numa única passada do ffmpeg (segment muxer). Devolve os chunks em ordem."""
    ff = util.ffmpeg_command()
    if ff is None:
        raise RuntimeError("ffmpeg ausente no PATH — necessário para a transcrição na nuvem")
    pattern = str(out_dir / "chunk_%03d.opus")
    cmd = ff + [
        "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
        "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k", "-application", "voip",
        "-f", "segment", "-segment_time", str(_CHUNK_SECONDS), pattern,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                       creationflags=_CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou ao fatiar o áudio (rc={r.returncode}): {(r.stderr or '')[:200]}")
    return sorted(out_dir.glob("chunk_*.opus"))


class CloudTranscriptionProvider:
    """Satisfaz TranscriptionProvider (scriba/transcription.py) usando STT na nuvem."""

    def __init__(self, cfg: Whisper):
        self.cfg = cfg
        self.device_used: str | None = "cloud"

    def ensure_loaded(self) -> str:
        if not (self.cfg.cloud_api_key or "").strip():
            raise RuntimeError("transcrição na nuvem sem chave de API — configure na aba Gravação")
        if util.ffmpeg_command() is None:
            raise RuntimeError("ffmpeg ausente no PATH — necessário para a transcrição na nuvem")
        self.device_used = "cloud"
        return "cloud"

    def transcribe(self, wav, on_progress: Callable[[float], None] | None = None) -> list[Segment]:
        wav = Path(wav)
        base = (self.cfg.cloud_base_url or _GROQ_DEFAULT).rstrip("/")
        url = f"{base}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.cfg.cloud_api_key}"}
        fields = {"model": self.cfg.cloud_model, "response_format": "verbose_json"}
        if (self.cfg.language or "").strip():
            fields["language"] = self.cfg.language

        out: list[Segment] = []
        with tempfile.TemporaryDirectory(prefix="scriba_stt_") as td:
            chunks = _chunk_to_opus(wav, Path(td))
            if not chunks:
                raise RuntimeError(f"nenhum trecho gerado de {wav.name} (áudio vazio?)")
            for i, chunk in enumerate(chunks):
                data = _http_multipart(
                    url, fields, "file", chunk.name, chunk.read_bytes(), "audio/ogg",
                    headers=headers, timeout=_HTTP_TIMEOUT,
                )
                if data is None:
                    raise RuntimeError(f"falha ao transcrever {chunk.name} na nuvem")
                offset = i * _CHUNK_SECONDS  # trechos são relativos ao próprio chunk
                for s in (data.get("segments") or []):
                    text = (s.get("text") or "").strip()
                    if text:
                        out.append(Segment(start=float(s["start"]) + offset,
                                           end=float(s["end"]) + offset, text=text))
                if on_progress:
                    on_progress(float((i + 1) * _CHUNK_SECONDS))
        return out

    def close(self) -> None:
        pass


def test_connection(cfg: Whisper | None = None) -> tuple[bool, str]:
    """Valida o STT na nuvem SEM enviar áudio (não há round-trip barato de STT): checa
    chave + ffmpeg e tenta um GET {base}/models autenticado. Shape igual a ai.test_connection."""
    if cfg is None:
        from .config import load
        cfg = load().whisper
    if not (cfg.cloud_api_key or "").strip():
        return (False, "sem chave de API — informe a chave do provedor")
    if util.ffmpeg_command() is None:
        return (False, "ffmpeg ausente no PATH (necessário para transcodar o áudio)")
    base = (cfg.cloud_base_url or _GROQ_DEFAULT).rstrip("/")
    try:
        req = urllib.request.Request(f"{base}/models", method="GET")
        req.add_header("Authorization", f"Bearer {cfg.cloud_api_key}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return (True, f"endpoint respondeu ({base})")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return (False, "chave rejeitada (401/403) — confira a chave da API")
        return (False, f"HTTP {e.code} em {base}/models")
    except Exception as e:
        return (False, f"não alcancei {base} ({e})")
