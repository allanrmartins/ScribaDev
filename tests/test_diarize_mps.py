"""Diarização no Metal (MPS) do Apple Silicon (#203).

Antes só existia o ramo cuda: num Mac o pyannote rodava inteiro em CPU. Estes
testes travam a escolha do device (cuda → mps → cpu, --cpu manda), o rótulo que
log/doctor mostram, e o contrato de segurança: o MPS não tem a quilometragem do
CUDA, então uma falha logo no início refaz a diarização em CPU em vez de pular
bloco a bloco e devolver uma call sem vozes. Tudo com um torch de mentira - o
host dos testes não tem Metal.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import diarize  # noqa: E402
from scriba.config import Diarization  # noqa: E402

_HAS_NUMPY = importlib.util.find_spec("numpy") is not None


def _fake_torch(cuda: bool = False, mps: bool | None = False, version: str = "2.14.0"):
    """torch de mentira: só o que diarize() toca. mps=None = torch sem o atributo
    backends.mps (build antigo)."""
    t = types.SimpleNamespace()
    t.__version__ = version
    t.cuda = types.SimpleNamespace(
        is_available=lambda: cuda,
        get_device_name=lambda i: "NVIDIA Fake 3060",
        empty_cache=mock.Mock(),
        OutOfMemoryError=type("OutOfMemoryError", (RuntimeError,), {}),
    )
    t.backends = types.SimpleNamespace()
    if mps is not None:
        t.backends.mps = types.SimpleNamespace(is_available=lambda: mps)
    t.mps = types.SimpleNamespace(empty_cache=mock.Mock())
    t.device = lambda name: name
    return t


class PickDeviceTests(unittest.TestCase):
    def test_cuda_tem_prioridade(self):
        self.assertEqual(diarize.pick_device(_fake_torch(cuda=True, mps=True)), "cuda")

    def test_sem_cuda_com_mps_vai_de_metal(self):
        # o caso da #203: Apple Silicon, torch sem CUDA, backend MPS disponível
        self.assertEqual(diarize.pick_device(_fake_torch(cuda=False, mps=True)), "mps")

    def test_sem_acelerador_e_cpu(self):
        self.assertEqual(diarize.pick_device(_fake_torch(cuda=False, mps=False)), "cpu")

    def test_torch_sem_backend_mps_e_cpu(self):
        self.assertEqual(diarize.pick_device(_fake_torch(cuda=False, mps=None)), "cpu")

    def test_force_cpu_manda_em_tudo(self):
        # `scribadev transcribe --cpu` (#115) vale p/ o Metal também
        self.assertEqual(diarize.pick_device(_fake_torch(cuda=True, mps=True), force_cpu=True), "cpu")
        self.assertEqual(diarize.pick_device(_fake_torch(cuda=False, mps=True), force_cpu=True), "cpu")

    def test_backend_que_explode_ao_consultar_e_cpu(self):
        t = _fake_torch(cuda=False, mps=False)
        t.backends.mps = types.SimpleNamespace(is_available=mock.Mock(side_effect=RuntimeError("boom")))
        self.assertEqual(diarize.pick_device(t), "cpu")


class DeviceLabelTests(unittest.TestCase):
    def test_rotulos(self):
        t = _fake_torch(cuda=True)
        self.assertEqual(diarize.device_label("cuda", t), "cuda (NVIDIA Fake 3060)")
        self.assertEqual(diarize.device_label("mps", t), "GPU Metal (MPS)")
        self.assertEqual(diarize.device_label("cpu", t), "CPU")

    def test_cuda_sem_nome_nao_quebra(self):
        t = _fake_torch(cuda=True)
        t.cuda.get_device_name = mock.Mock(side_effect=RuntimeError("driver"))
        self.assertEqual(diarize.device_label("cuda", t), "cuda (GPU)")


class FreeDeviceCacheTests(unittest.TestCase):
    def test_mps_esvazia_o_cache_do_metal(self):
        t = _fake_torch(cuda=False, mps=True)
        with mock.patch.dict(sys.modules, {"torch": t}):
            diarize._free_device_cache()
        t.mps.empty_cache.assert_called_once()
        t.cuda.empty_cache.assert_not_called()

    def test_cuda_esvazia_o_cache_cuda(self):
        t = _fake_torch(cuda=True, mps=True)
        with mock.patch.dict(sys.modules, {"torch": t}):
            diarize._free_device_cache()
        t.cuda.empty_cache.assert_called_once()
        t.mps.empty_cache.assert_not_called()

    def test_sem_torch_importado_nao_importa(self):
        # #196: nunca importar o torch por conta própria aqui
        with mock.patch.dict(sys.modules, {"torch": None}):
            diarize._free_device_cache()  # não levanta


class _FakeWav:
    def __init__(self, n: int):
        self.shape = (1, n)

    def __getitem__(self, key):
        return self

    def clone(self):
        return self


@unittest.skipUnless(_HAS_NUMPY, "numpy não instalado")
class StrictFirstBlockTests(unittest.TestCase):
    """_diarize_chunked(strict_first_block=True): 1º bloco falhando SOBE; do 2º em
    diante vale a resiliência de sempre (pula o bloco)."""

    SR = 16000
    CHUNK_S = 60

    def setUp(self):
        self._orig = (diarize._run_pipe, diarize._free_device_cache, diarize._thermal_pause)
        diarize._free_device_cache = lambda: None
        diarize._thermal_pause = lambda: None
        self.audio = {"waveform": _FakeWav(3 * self.CHUNK_S * self.SR), "sample_rate": self.SR}

    def tearDown(self):
        diarize._run_pipe, diarize._free_device_cache, diarize._thermal_pause = self._orig

    def _pipe_that_fails_on(self, bad_blocks):
        calls = {"n": 0}

        def run(pipe, audio, kwargs):
            i = calls["n"]
            calls["n"] += 1
            if i in bad_blocks:
                raise RuntimeError("The operator 'aten::foo' is not currently implemented for the MPS device")
            return ([(0.0, 1.0, "SPEAKER_00")], {"SPEAKER_00": [1.0, 0.0]})

        return run

    def test_primeiro_bloco_falhando_sobe_no_modo_estrito(self):
        diarize._run_pipe = self._pipe_that_fails_on({0})
        with self.assertRaises(RuntimeError):
            diarize._diarize_chunked(None, self.audio, self.SR, self.CHUNK_S, strict_first_block=True)

    def test_primeiro_bloco_falhando_e_pulado_sem_o_modo_estrito(self):
        # o comportamento de sempre (CUDA/CPU): resiliência por bloco
        diarize._run_pipe = self._pipe_that_fails_on({0})
        out = diarize._diarize_chunked(None, self.audio, self.SR, self.CHUNK_S)
        self.assertIsNotNone(out)
        self.assertEqual(len(out[0]), 2)  # 2 blocos bons

    def test_segundo_bloco_falhando_e_pulado_mesmo_no_modo_estrito(self):
        diarize._run_pipe = self._pipe_that_fails_on({1})
        out = diarize._diarize_chunked(None, self.audio, self.SR, self.CHUNK_S, strict_first_block=True)
        self.assertEqual(len(out[0]), 2)


@unittest.skipUnless(_HAS_NUMPY, "numpy não instalado")
class DiarizeMpsFallbackTests(unittest.TestCase):
    """diarize() de ponta a ponta com torch/pyannote de mentira: no MPS, o pipe vai
    p/ 'mps'; se a diarização falhar, volta p/ 'cpu' e refaz - a nota sai com vozes."""

    SR = 16000

    def _run_diarize(self, torch, run_pipe_side_effects, chunk_minutes=0, force_cpu=False):
        pipe = mock.Mock()
        fake_pa = types.SimpleNamespace(Pipeline=types.SimpleNamespace(
            from_pretrained=lambda *a, **k: pipe))
        run_pipe = mock.Mock(side_effect=run_pipe_side_effects)
        audio = {"waveform": _FakeWav(30 * self.SR), "sample_rate": self.SR}  # 30 s: caminho único
        meta: dict = {}
        printed: list[str] = []
        with mock.patch.dict(sys.modules, {"torch": torch, "pyannote": types.ModuleType("pyannote"),
                                           "pyannote.audio": fake_pa}), \
                mock.patch.object(diarize, "_run_pipe", run_pipe), \
                mock.patch.object(diarize, "_load_waveform", return_value=audio), \
                mock.patch.object(diarize, "_cap_pyannote_vram", lambda *a, **k: None), \
                mock.patch.object(diarize, "_uncap_pyannote_vram", lambda *a, **k: None), \
                mock.patch("builtins.print", lambda *a, **k: printed.append(" ".join(str(x) for x in a))):
            res = diarize.diarize(Path("call.wav"), Diarization(enabled=True, hf_token="hf_x",
                                                                  chunk_minutes=chunk_minutes),
                                  meta=meta, force_cpu=force_cpu)
        return res, pipe, run_pipe, meta, printed

    _TURNS = ([(0.0, 2.0, "SPEAKER_00"), (2.0, 4.0, "SPEAKER_01")], {})

    def test_mps_disponivel_move_o_pipe_para_o_metal(self):
        res, pipe, run_pipe, meta, printed = self._run_diarize(
            _fake_torch(cuda=False, mps=True), [self._TURNS])
        pipe.to.assert_called_once_with("mps")
        self.assertEqual(len(res.turns), 2)
        self.assertNotIn("diarization_error", meta)
        self.assertTrue(any("diarização em GPU Metal (MPS)" in p for p in printed), printed)

    def test_mps_falhando_refaz_em_cpu(self):
        res, pipe, run_pipe, meta, printed = self._run_diarize(
            _fake_torch(cuda=False, mps=True),
            [RuntimeError("MPS backend out of memory"), self._TURNS])
        self.assertEqual([c.args[0] for c in pipe.to.call_args_list], ["mps", "cpu"])
        self.assertEqual(run_pipe.call_count, 2)
        self.assertEqual(len(res.turns), 2)             # a nota sai COM vozes
        self.assertNotIn("diarization_error", meta)     # não é erro: é fallback
        self.assertTrue(any("refazendo em CPU" in p for p in printed), printed)

    def test_falha_em_cpu_nao_tenta_de_novo(self):
        res, pipe, run_pipe, meta, printed = self._run_diarize(
            _fake_torch(cuda=False, mps=False), [RuntimeError("waveform vazio")])
        self.assertIsNone(res)
        self.assertEqual(run_pipe.call_count, 1)
        self.assertIn("diarization_error", meta)

    def test_force_cpu_pula_o_metal(self):
        res, pipe, run_pipe, meta, printed = self._run_diarize(
            _fake_torch(cuda=False, mps=True), [self._TURNS], force_cpu=True)
        pipe.to.assert_not_called()
        self.assertTrue(any("forçada com --cpu" in p for p in printed), printed)

    def test_variavel_de_fallback_do_mps_e_exportada_antes_do_torch(self):
        import os

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PYTORCH_ENABLE_MPS_FALLBACK", None)
            self._run_diarize(_fake_torch(cuda=False, mps=True), [self._TURNS])
            self.assertEqual(os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"), "1")

    def test_variavel_ja_exportada_pelo_usuario_e_respeitada(self):
        import os

        with mock.patch.dict(os.environ, {"PYTORCH_ENABLE_MPS_FALLBACK": "0"}):
            self._run_diarize(_fake_torch(cuda=False, mps=True), [self._TURNS])
            self.assertEqual(os.environ["PYTORCH_ENABLE_MPS_FALLBACK"], "0")


if __name__ == "__main__":
    unittest.main()
