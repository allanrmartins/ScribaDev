"""Sonda de dispositivos de áudio, executada em processo isolado.

O PortAudio pode ABORTAR o processo com um assert de CRT (pa_front.c) quando os
dispositivos mudam durante a enumeração — incapturável em Python. Rodando a
sonda num subprocesso descartável, o app principal nunca cai por causa disso.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    import pyaudiowpatch as pyaudio

    pa = pyaudio.PyAudio()
    try:
        mic = pa.get_default_input_device_info()["name"]
        loopback = pa.get_default_wasapi_loopback()["name"]
    finally:
        pa.terminate()
    print(json.dumps({"mic": str(mic), "loopback": str(loopback)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
