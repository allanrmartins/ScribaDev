"""Testes de scriba.transcriber (#6): VAD opt-in e kwargs de decodificação.

O topo de scriba.transcriber é leve (faster_whisper só é importado dentro de
ensure_loaded/_run_batched), então estes testes rodam sem GPU nem modelo — usam
um modelo falso que captura os kwargs passados.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.config import Whisper  # noqa: E402
from scriba.transcriber import Transcriber  # noqa: E402


class _FakeModel:
    """Captura os kwargs de cada transcribe() e devolve um resultado vazio."""

    def __init__(self):
        self.calls: list[dict] = []

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        return iter(()), None  # (segments, info)


class VadParametersTests(unittest.TestCase):
    def _tr(self, **kw):
        return Transcriber(Whisper(**kw))

    def test_tudo_zero_vira_none(self):
        self.assertIsNone(self._tr()._vad_parameters())  # default = sem mudança

    def test_so_min_silence(self):
        self.assertEqual(self._tr(vad_min_silence_ms=500)._vad_parameters(),
                         {"min_silence_duration_ms": 500})

    def test_so_threshold(self):
        self.assertEqual(self._tr(vad_threshold=0.35)._vad_parameters(), {"threshold": 0.35})

    def test_ambos(self):
        self.assertEqual(self._tr(vad_min_silence_ms=400, vad_threshold=0.4)._vad_parameters(),
                         {"min_silence_duration_ms": 400, "threshold": 0.4})


class RunKwargsTests(unittest.TestCase):
    def _ready_tr(self, **kw):
        # batch_size=0 força o caminho normal (sem BatchedInferencePipeline)
        tr = Transcriber(Whisper(batch_size=0, **kw))
        tr.model = _FakeModel()
        tr.device_used = "cpu"  # ensure_loaded retorna cedo: self.model já existe
        return tr

    def test_beam_size_e_passado(self):
        tr = self._ready_tr(beam_size=2)
        tr._run(Path("x.wav"), None)
        self.assertEqual(tr.model.calls[0]["beam_size"], 2)

    def test_beam_size_zero_cai_no_default(self):
        tr = self._ready_tr(beam_size=0)
        tr._run(Path("x.wav"), None)
        self.assertEqual(tr.model.calls[0]["beam_size"], 3)  # 0 => default 3, não greedy acidental

    def test_beam_size_1_greedy_preservado(self):
        tr = self._ready_tr(beam_size=1)
        tr._run(Path("x.wav"), None)
        self.assertEqual(tr.model.calls[0]["beam_size"], 1)

    def test_vad_nao_passa_por_padrao(self):
        tr = self._ready_tr()
        tr._run(Path("x.wav"), None)
        self.assertNotIn("vad_parameters", tr.model.calls[0])  # default = lib decide

    def test_vad_passa_quando_configurado(self):
        tr = self._ready_tr(vad_min_silence_ms=300)
        tr._run(Path("x.wav"), None)
        self.assertEqual(tr.model.calls[0]["vad_parameters"], {"min_silence_duration_ms": 300})


class CargaLocalPrimeiroTests(unittest.TestCase):
    """#176: faster-whisper resolve o NOME do modelo pelo Hugging Face a cada carga,
    mesmo com tudo em cache. Numa rede que engasga isso pendura a transcrição sem
    timeout - 0% de CPU, nenhuma linha nova no log. Cache primeiro; rede só se faltar."""

    def setUp(self):
        import types

        self.tentativas: list[dict] = []
        fake = types.ModuleType("faster_whisper")

        class _FakeWhisperModel:
            def __init__(_s, model, **kw):
                self.tentativas.append({"model": model, **kw})
                if kw.get("local_files_only") and getattr(self, "sem_cache", False):
                    raise OSError("modelo não está no cache")

        fake.WhisperModel = _FakeWhisperModel
        self._salvo = sys.modules.get("faster_whisper")
        sys.modules["faster_whisper"] = fake
        self.addCleanup(self._restaurar)

    def _restaurar(self):
        if self._salvo is None:
            sys.modules.pop("faster_whisper", None)
        else:
            sys.modules["faster_whisper"] = self._salvo

    def test_com_cache_nao_toca_a_rede(self):
        tr = Transcriber(Whisper(device="cpu"))
        tr._abrir_modelo("cpu", "int8")
        self.assertEqual(len(self.tentativas), 1)
        self.assertTrue(self.tentativas[0]["local_files_only"])

    def test_sem_cache_cai_para_a_rede(self):
        self.sem_cache = True
        tr = Transcriber(Whisper(device="cpu"))
        tr._abrir_modelo("cpu", "int8", cpu_threads=2)
        self.assertEqual(len(self.tentativas), 2)
        self.assertTrue(self.tentativas[0]["local_files_only"])
        self.assertNotIn("local_files_only", self.tentativas[1])   # 2ª tentativa: com rede
        self.assertEqual(self.tentativas[1]["cpu_threads"], 2)     # kwargs preservados


if __name__ == "__main__":
    unittest.main(verbosity=2)
