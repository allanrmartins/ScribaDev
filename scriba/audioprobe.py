"""Sonda de dispositivos de áudio, executada em processo isolado.

O PortAudio pode ABORTAR o processo com um assert de CRT (pa_front.c) quando os
dispositivos mudam durante a enumeração — incapturável em Python. Rodando a
sonda num subprocesso descartável, o app principal nunca cai por causa disso.

Dois modos:
- (sem argumento) → {"mic","loopback"}: nomes dos dispositivos PADRÃO (usado na
  pré-checagem antes de gravar).
- "list"          → {"mics":[...],"loopbacks":[...],"default_mic","default_loopback"}:
  listas completas para os seletores da UI (Configurações).
"""

from __future__ import annotations

import json
import sys


def _dedup(names: list[str]) -> list[str]:
    """Remove nomes repetidos preservando a ordem (o WASAPI às vezes duplica)."""
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _list(pa) -> dict:
    import pyaudiowpatch as pyaudio

    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
    mics = []
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d["hostApi"] == wasapi and not d.get("isLoopbackDevice") and int(d["maxInputChannels"]) > 0:
            mics.append(str(d["name"]))
    loopbacks = [str(d["name"]) for d in pa.get_loopback_device_info_generator()]

    def _default(getter) -> str:
        try:
            return str(getter()["name"])
        except Exception:
            return ""

    return {
        "mics": _dedup(mics),
        "loopbacks": _dedup(loopbacks),
        "default_mic": _default(pa.get_default_input_device_info),
        "default_loopback": _default(pa.get_default_wasapi_loopback),
    }


def main(mode: str | None = None) -> int:
    """`mode` explícito vem do subcomando oculto `scribadev audioprobe` (caminho do
    app congelado, que não tem `python -m`); None = lê do argv (`python -m`)."""
    if mode is None:
        mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if sys.platform == "darwin":
        # No macOS a sonda não precisa do isolamento anti-abort (pathologia do
        # PortAudio/WASAPI), mas manter o subprocesso preserva os call sites e
        # deixa o import do sounddevice fora do processo da GUI até precisar.
        from . import recorder_mac

        out = recorder_mac.probe_list() if mode == "list" else recorder_mac.probe_defaults()
        print(json.dumps(out, ensure_ascii=False))
        return 0
    import pyaudiowpatch as pyaudio

    pa = pyaudio.PyAudio()
    try:
        if mode == "list":
            out = _list(pa)
        else:
            out = {
                "mic": str(pa.get_default_input_device_info()["name"]),
                "loopback": str(pa.get_default_wasapi_loopback()["name"]),
            }
    finally:
        pa.terminate()
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
