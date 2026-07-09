"""#99: integridade da fixture de reunião (transcrição E2E do Marco 1, épico #104).

A fixture é o insumo do critério de aceite no Linux (`scribadev transcribe`).
Regras que este teste trava:
- meta.json mínimo com SÓ o stream `mic` (loopback acionaria diarização);
- mic.wav no layout canônico de 44 bytes do CrashSafeWav (fmt de 16 bytes) —
  o repair_wav_header patcha os offsets 4 e 40 às cegas e CORROMPE WAVs com
  fmt de 18 bytes (foi exatamente o bug ao gerar a fixture via SAPI).
"""

import json
import struct
import unittest
import wave
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "reuniao_exemplo"


class TestFixtureReuniao(unittest.TestCase):
    def test_meta_minimo_so_stream_mic(self):
        meta = json.loads((FIXTURE / "meta.json").read_text(encoding="utf-8"))
        streams = meta["streams"]
        self.assertEqual(list(streams), ["mic"], "só o stream mic — loopback acionaria diarização")
        self.assertEqual(streams["mic"]["file"], "mic.wav")
        self.assertTrue((FIXTURE / "mic.wav").exists())

    def test_wav_header_canonico_44_bytes(self):
        raw = (FIXTURE / "mic.wav").read_bytes()
        self.assertEqual(raw[:4], b"RIFF")
        self.assertEqual(raw[8:12], b"WAVE")
        self.assertEqual(raw[12:16], b"fmt ")
        fmt_size = struct.unpack("<I", raw[16:20])[0]
        self.assertEqual(fmt_size, 16, "fmt de 16 bytes — layout que o repair_wav_header espera")
        self.assertEqual(raw[36:40], b"data", "chunk data no offset 36 (header de 44 bytes)")
        data_size = struct.unpack("<I", raw[40:44])[0]
        self.assertEqual(len(raw), 44 + data_size, "tamanho do data bate com o arquivo")

    def test_wav_formato_do_app(self):
        with wave.open(str(FIXTURE / "mic.wav")) as w:
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getframerate(), 16000)
            self.assertEqual(w.getsampwidth(), 2)  # PCM 16-bit
            dur = w.getnframes() / w.getframerate()
        self.assertGreater(dur, 5.0, "fala suficiente para o critério de aceite")
        self.assertLess(dur, 60.0, "fixture curta — transcrição em CPU precisa ser rápida")


if __name__ == "__main__":
    unittest.main()
