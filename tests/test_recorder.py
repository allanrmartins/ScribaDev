"""Testes de scriba.recorder: seleção do microfone (#22) e medidor de nível (#22).

Não abre áudio real: usa um PyAudio falso e um stub de `pyaudiowpatch` (só a
constante paWASAPI), já que _pick_mic faz `import pyaudiowpatch` lazy.
"""

import struct
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import audioprobe, recorder  # noqa: E402


def _dev(name, idx, ch=2, host=0, loop=False):
    return {"name": name, "index": idx, "maxInputChannels": ch, "hostApi": host, "isLoopbackDevice": loop}


def _cfg(mic="", loop=""):
    return types.SimpleNamespace(mic_device=mic, loopback_device=loop)


class FakePa:
    def __init__(self, devices, default):
        self._devices = devices
        self._default = default

    def get_host_api_info_by_type(self, _t):
        return {"index": 0}

    def get_device_count(self):
        return len(self._devices)

    def get_device_info_by_index(self, i):
        return self._devices[i]

    def get_default_input_device_info(self):
        return self._default


class PickMicTests(unittest.TestCase):
    def setUp(self):
        self._had = sys.modules.get("pyaudiowpatch")
        sys.modules["pyaudiowpatch"] = types.SimpleNamespace(paWASAPI=0)

    def tearDown(self):
        if self._had is not None:
            sys.modules["pyaudiowpatch"] = self._had
        else:
            sys.modules.pop("pyaudiowpatch", None)

    def test_vazio_usa_o_padrao(self):
        pa = FakePa([_dev("Headset", 1)], default=_dev("Mic Padrão", 9))
        self.assertEqual(recorder._pick_mic(_cfg(""), pa)["index"], 9)

    def test_casa_por_substring_case_insensitive(self):
        pa = FakePa([_dev("Microfone Interno", 1), _dev("Jabra Evolve Headset", 2)], default=_dev("X", 9))
        self.assertEqual(recorder._pick_mic(_cfg("jabra"), pa)["index"], 2)

    def test_nao_casa_cai_no_padrao(self):
        pa = FakePa([_dev("Microfone Interno", 1)], default=_dev("X", 9))
        self.assertEqual(recorder._pick_mic(_cfg("inexistente"), pa)["index"], 9)

    def test_ignora_loopback_e_dispositivo_sem_canal(self):
        pa = FakePa([_dev("Alto-falante Loop", 1, loop=True), _dev("Saída Muda", 2, ch=0)], default=_dev("X", 9))
        self.assertEqual(recorder._pick_mic(_cfg("loop"), pa)["index"], 9)
        self.assertEqual(recorder._pick_mic(_cfg("muda"), pa)["index"], 9)

    def test_enumeracao_que_explode_cai_no_padrao(self):
        class Boom(FakePa):
            def get_device_count(self):
                raise OSError("PortAudio surtou")

        self.assertEqual(recorder._pick_mic(_cfg("jabra"), Boom([], _dev("Mic Padrão", 9)))["index"], 9)


class PeakLevelTests(unittest.TestCase):
    def test_silencio_eh_zero(self):
        self.assertEqual(recorder.peak_level(b"\x00\x00" * 256), 0.0)

    def test_vazio_eh_zero(self):
        self.assertEqual(recorder.peak_level(b""), 0.0)

    def test_pico_maximo_eh_um(self):
        # -32768 é o extremo de int16; abs/32768 = 1.0
        self.assertAlmostEqual(recorder.peak_level(struct.pack("<hhh", 0, -32768, 100)), 1.0, places=3)

    def test_meia_escala(self):
        self.assertAlmostEqual(recorder.peak_level(struct.pack("<hh", 16384, -8000)), 16384 / 32768.0, places=3)

    def test_buffer_de_tamanho_impar_nao_quebra(self):
        self.assertIsInstance(recorder.peak_level(b"\x00\x01\x02"), float)


class DedupTests(unittest.TestCase):
    def test_preserva_ordem_sem_repetir(self):
        self.assertEqual(audioprobe._dedup(["a", "b", "a", "c", "b"]), ["a", "b", "c"])

    def test_vazio(self):
        self.assertEqual(audioprobe._dedup([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
