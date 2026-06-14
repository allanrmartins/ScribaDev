"""Pré-download do modelo Whisper (chamado pelo setup.ps1 via `python -m scriba.predownload`)."""

from __future__ import annotations


def main() -> int:
    from faster_whisper import WhisperModel

    from .config import load

    model = load().whisper.model
    print(f"baixando/validando modelo: {model}")
    WhisperModel(model, device="cpu", compute_type="int8")
    print("modelo OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
