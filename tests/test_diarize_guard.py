"""Testes da proteção de GPU na diarização (#115).

Em produção, blocos falhando com cudaErrorUnknown foram "pulados" e a diarização
seguiu martelando uma GPU de contexto já corrompido — escalou para TDR/perda de
vídeo. Estes testes travam o novo contrato: erro de driver/contexto ABORTA tudo
(sem mais submissões à GPU), OOM segue recuperável por bloco, e falhas em série
desistem mantendo o que já foi separado.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import diarize  # noqa: E402

_HAS_NUMPY = importlib.util.find_spec("numpy") is not None


class ClassificacaoDeErroTests(unittest.TestCase):
    def test_oom_nao_e_erro_de_contexto(self):
        self.assertFalse(diarize._is_cuda_context_error(RuntimeError("CUDA out of memory.")))

    def test_alloc_failed_nao_e_erro_de_contexto(self):
        self.assertFalse(diarize._is_cuda_context_error(
            RuntimeError("CUBLAS_STATUS_ALLOC_FAILED when calling cublasCreate")))

    def test_cuda_unknown_error_e_erro_de_contexto(self):
        # a mensagem real do incidente em produção
        self.assertTrue(diarize._is_cuda_context_error(
            RuntimeError("CUDA error: unknown error\nCUDA kernel errors might be...")))

    def test_device_side_assert_e_erro_de_contexto(self):
        self.assertTrue(diarize._is_cuda_context_error(
            RuntimeError("CUDA error: device-side assert triggered")))

    def test_illegal_memory_access_e_erro_de_contexto(self):
        self.assertTrue(diarize._is_cuda_context_error(
            RuntimeError("CUDA error: an illegal memory access was encountered")))

    def test_erro_generico_nao_e_de_contexto(self):
        self.assertFalse(diarize._is_cuda_context_error(ValueError("waveform vazio")))


class _FakeWav:
    """Imita o tensor (1, N) só no que _diarize_chunked usa: shape e fatiamento."""

    def __init__(self, n: int):
        self.shape = (1, n)

    def __getitem__(self, key):
        return self

    def clone(self):
        return self


@unittest.skipUnless(_HAS_NUMPY, "numpy não instalado")
class ChunkedGuardTests(unittest.TestCase):
    """_diarize_chunked com _run_pipe/_free_cuda/_thermal_pause dublados."""

    SR = 16000
    CHUNK_S = 60

    def setUp(self):
        self._orig = (diarize._run_pipe, diarize._free_cuda, diarize._thermal_pause)
        diarize._free_cuda = lambda: None
        diarize._thermal_pause = lambda: None
        self.audio = {"waveform": _FakeWav(5 * self.CHUNK_S * self.SR),  # 5 blocos
                      "sample_rate": self.SR}

    def tearDown(self):
        diarize._run_pipe, diarize._free_cuda, diarize._thermal_pause = self._orig

    def _run(self):
        return diarize._diarize_chunked(None, self.audio, self.SR, self.CHUNK_S)

    def test_erro_de_contexto_aborta_tudo(self):
        chamadas = []

        def pipe_quebrado(pipe, audio, kwargs):
            chamadas.append(1)
            raise RuntimeError("CUDA error: unknown error")

        diarize._run_pipe = pipe_quebrado
        with self.assertRaises(diarize._CudaContextError):
            self._run()
        # abortou no 1º bloco: nenhuma submissão extra à GPU ferida
        self.assertEqual(len(chamadas), 1)

    def test_oom_em_serie_desiste_sem_insistir(self):
        chamadas = []

        def pipe_oom(pipe, audio, kwargs):
            chamadas.append(1)
            raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")

        diarize._run_pipe = pipe_oom
        turns, embeddings = self._run()
        self.assertEqual(turns, [])
        self.assertEqual(embeddings, {})
        # desistiu no teto de falhas consecutivas, não tentou os 5 blocos
        self.assertEqual(len(chamadas), diarize._MAX_CONSECUTIVE_FAILS)

    def test_bloco_bom_zera_o_contador_e_mantem_parciais(self):
        seq = iter(["ok", "oom", "oom", "ok", "ok"])

        def pipe_misto(pipe, audio, kwargs):
            if next(seq) == "oom":
                raise RuntimeError("CUDA out of memory.")
            return ([(0.0, 1.0, "SPEAKER_00")], {})

        diarize._run_pipe = pipe_misto
        turns, _ = self._run()
        # 2 falhas < teto (3) no meio não derrubam; os 3 blocos bons ficam
        self.assertEqual(len(turns), 3)

    def test_oom_espalhado_nao_desiste(self):
        seq = iter(["oom", "ok", "oom", "ok", "oom"])

        def pipe_intercalado(pipe, audio, kwargs):
            if next(seq) == "oom":
                raise RuntimeError("CUDA out of memory.")
            return ([(0.0, 1.0, "SPEAKER_00")], {})

        diarize._run_pipe = pipe_intercalado
        turns, _ = self._run()
        self.assertEqual(len(turns), 2)  # os 2 blocos bons, sem bail


class ForceCpuTests(unittest.TestCase):
    def test_diarize_desabilitada_ignora_force_cpu(self):
        from scriba.config import Diarization

        cfg = Diarization(enabled=False)
        self.assertIsNone(diarize.diarize(Path("nao-existe.wav"), cfg, force_cpu=True))


if __name__ == "__main__":
    unittest.main()
