"""Testes de scriba.recorder._pick_mic — seleção do microfone por nome (#22).

Não abre áudio real: usa um PyAudio falso e um stub de `pyaudiowpatch` (só a
constante paWASAPI), já que o _pick_mic faz `import pyaudiowpatch` lazy.
"""

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import audioprobe  # noqa: E402
from scriba.recorder import Recording  # noqa: E402


def _dev(name, idx, ch=2, host=0, loop=False):
    return {"name": name, "index": idx, "maxInputChannels": ch, "hostApi": host, "isLoopbackDevice": loop}


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
        # _pick_mic faz `import pyaudiowpatch as pyaudio; pyaudio.paWASAPI`
        self._had = sys.modules.get("pyaudiowpatch")
        sys.modules["pyaudiowpatch"] = types.SimpleNamespace(paWASAPI=0)

    def tearDown(self):
        if self._had is not None:
            sys.modules["pyaudiowpatch"] = self._had
        else:
            sys.modules.pop("pyaudiowpatch", None)

    def _rec(self, mic_device):
        r = Recording.__new__(Recording)  # sem __init__ (não abre PyAudio)
        r.cfg = types.SimpleNamespace(mic_device=mic_device)
        return r

    def test_vazio_usa_o_padrao(self):
        pa = FakePa([_dev("Headset", 1)], default=_dev("Mic Padrão", 9))
        self.assertEqual(self._rec("")._pick_mic(pa)["index"], 9)

    def test_casa_por_substring_case_insensitive(self):
        pa = FakePa([_dev("Microfone Interno", 1), _dev("Jabra Evolve Headset", 2)], default=_dev("X", 9))
        self.assertEqual(self._rec("jabra")._pick_mic(pa)["index"], 2)

    def test_nao_casa_cai_no_padrao(self):
        pa = FakePa([_dev("Microfone Interno", 1)], default=_dev("X", 9))
        self.assertEqual(self._rec("inexistente")._pick_mic(pa)["index"], 9)

    def test_ignora_loopback_e_dispositivo_sem_canal_de_entrada(self):
        pa = FakePa([_dev("Alto-falante Loop", 1, loop=True), _dev("Saída Muda", 2, ch=0)], default=_dev("X", 9))
        # 'loop' casaria o loopback e 'muda' o sem-canal — ambos filtrados → padrão
        self.assertEqual(self._rec("loop")._pick_mic(pa)["index"], 9)
        self.assertEqual(self._rec("muda")._pick_mic(pa)["index"], 9)

    def test_enumeracao_que_explode_cai_no_padrao(self):
        class Boom(FakePa):
            def get_device_count(self):
                raise OSError("PortAudio surtou")

        pa = Boom([], default=_dev("Mic Padrão", 9))
        self.assertEqual(self._rec("jabra")._pick_mic(pa)["index"], 9)


class DedupTests(unittest.TestCase):
    def test_preserva_ordem_sem_repetir(self):
        self.assertEqual(audioprobe._dedup(["a", "b", "a", "c", "b"]), ["a", "b", "c"])

    def test_vazio(self):
        self.assertEqual(audioprobe._dedup([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
