"""Testes de scriba.recorder: seleção do microfone (#22) e medidor de nível (#22).

Não abre áudio real: usa um PyAudio falso e um stub de `pyaudiowpatch` (só a
constante paWASAPI), já que _pick_mic faz `import pyaudiowpatch` lazy.
"""

import struct
import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import audioprobe, recorder  # noqa: E402
from scriba.recorder import _StreamRecorder  # noqa: E402


class _FakeStream:
    def __init__(self, active=True, raises=False):
        self._active = active
        self._raises = raises

    def is_active(self):
        if self._raises:
            raise OSError("device removido")
        return self._active

    def stop_stream(self):
        pass

    def close(self):
        pass


class _FakePaOpen:
    def __init__(self, raises=False):
        self.raises = raises
        self.opened = None

    def open(self, **kw):
        if self.raises:
            raise OSError("não consegui abrir")
        self.opened = kw
        return types.SimpleNamespace(stop_stream=lambda: None, close=lambda: None)


def _sr(rate=48000, channels=2, pad_silence=False, active=True, last_age=0.0, active_raises=False):
    sr = _StreamRecorder.__new__(_StreamRecorder)  # sem __init__ (não abre PortAudio)
    sr.rate, sr.channels, sr.pad_silence = rate, channels, pad_silence
    sr.device_name = "Antigo"
    sr.last_data = time.monotonic() - last_age
    sr._callback = lambda *a: None
    sr.stream = _FakeStream(active=active, raises=active_raises)
    return sr


def _info(rate=48000, ch=2, idx=5, name="Novo Device"):
    return {"defaultSampleRate": rate, "maxInputChannels": ch, "index": idx, "name": name}


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


class ReopenTests(unittest.TestCase):
    """_StreamRecorder.reopen: troca o device só se o formato bater com o WAV aberto."""

    def setUp(self):
        self._had = sys.modules.get("pyaudiowpatch")
        sys.modules["pyaudiowpatch"] = types.SimpleNamespace(paInt16=8)

    def tearDown(self):
        if self._had is not None:
            sys.modules["pyaudiowpatch"] = self._had
        else:
            sys.modules.pop("pyaudiowpatch", None)

    def test_reabre_quando_formato_bate(self):
        sr, pa = _sr(rate=48000, channels=2), _FakePaOpen()
        self.assertTrue(sr.reopen(pa, _info(rate=48000, ch=2, idx=7, name="Novo Mic")))
        self.assertEqual(sr.device_name, "Novo Mic")
        self.assertEqual(pa.opened["input_device_index"], 7)

    def test_rejeita_rate_diferente_sem_abrir(self):
        sr, pa = _sr(rate=48000, channels=2), _FakePaOpen()
        old = sr.stream
        self.assertFalse(sr.reopen(pa, _info(rate=16000)))
        self.assertIs(sr.stream, old)     # mantém o stream antigo
        self.assertIsNone(pa.opened)      # nem tentou abrir

    def test_rejeita_canais_diferentes(self):
        self.assertFalse(_sr(rate=48000, channels=1).reopen(_FakePaOpen(), _info(rate=48000, ch=2)))

    def test_open_que_falha_mantem_o_antigo(self):
        sr = _sr(rate=48000, channels=2)
        old = sr.stream
        self.assertFalse(sr.reopen(_FakePaOpen(raises=True), _info()))
        self.assertIs(sr.stream, old)


class LooksDeadTests(unittest.TestCase):
    def test_ativo_e_com_dados_recentes_esta_vivo(self):
        self.assertFalse(_sr(active=True, last_age=0.0, pad_silence=False).looks_dead())

    def test_stream_inativo_esta_morto(self):
        self.assertTrue(_sr(active=False).looks_dead())

    def test_is_active_que_explode_conta_como_morto(self):
        self.assertTrue(_sr(active_raises=True).looks_dead())

    def test_mic_travado_sem_dados_esta_morto(self):
        self.assertTrue(_sr(active=True, last_age=99, pad_silence=False).looks_dead())

    def test_loopback_quieto_em_silencio_nao_eh_morte(self):
        # loopback (pad_silence=True) fica sem dados em silêncio legítimo — não é morte
        self.assertFalse(_sr(active=True, last_age=99, pad_silence=True).looks_dead())


class DedupTests(unittest.TestCase):
    def test_preserva_ordem_sem_repetir(self):
        self.assertEqual(audioprobe._dedup(["a", "b", "a", "c", "b"]), ["a", "b", "c"])

    def test_vazio(self):
        self.assertEqual(audioprobe._dedup([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
