"""Testes do seam de transcrição (#13): TranscriptionProvider + factory + pipeline.

Provam a abstração SEM faster-whisper: o pipeline roda contra um provider FAKE.
"""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import config, util  # noqa: E402
from scriba.config import Whisper  # noqa: E402
from scriba.transcriber import FasterWhisperProvider, Segment, Transcriber  # noqa: E402
from scriba.transcription import TranscriptionProvider, make_transcriber  # noqa: E402


class _FakeProvider:
    """Implementa o contrato TranscriptionProvider sem carregar modelo nenhum."""

    def __init__(self, segments):
        self._segments = segments
        self.device_used = "cpu"

    def ensure_loaded(self):
        return "cpu"

    def transcribe(self, wav, on_progress=None):
        return list(self._segments)

    def close(self):
        pass


class SeamTests(unittest.TestCase):
    def test_transcriber_conforma_ao_protocol(self):
        self.assertIsInstance(Transcriber(Whisper()), TranscriptionProvider)

    def test_fake_provider_conforma(self):
        self.assertIsInstance(_FakeProvider([]), TranscriptionProvider)

    def test_alias_faster_whisper_provider(self):
        self.assertIs(FasterWhisperProvider, Transcriber)

    def test_factory_devolve_provider_local(self):
        tr = make_transcriber(Whisper(), force_cpu=True)
        self.assertIsInstance(tr, Transcriber)
        self.assertIsInstance(tr, TranscriptionProvider)


# importar scriba.pipeline é leve (faster-whisper é deferido dentro de ensure_loaded)
try:
    import scriba.pipeline as pipeline
    _HAVE_PIPELINE = True
except Exception:
    _HAVE_PIPELINE = False


@unittest.skipUnless(_HAVE_PIPELINE, "scriba.pipeline indisponível")
class PipelineSeamTests(unittest.TestCase):
    """O pipeline produz transcript.json correto rodando contra um provider FAKE."""

    def setUp(self):
        # redireciona estado p/ tempdir (config default: diarização OFF) — não toca o AppData
        self.d = Path(tempfile.mkdtemp(prefix="scriba_stt_"))
        util.APP_DIR = self.d
        util.LOGS_DIR = self.d / "logs"
        util.CONFIG_PATH = self.d / "config.toml"
        # stub do recorder.repair_folder (evita importar pyaudiowpatch no checkout enxuto)
        self._real_recorder = sys.modules.get("scriba.recorder")
        stub = types.ModuleType("scriba.recorder")
        stub.repair_folder = lambda folder: None
        sys.modules["scriba.recorder"] = stub

    def tearDown(self):
        if self._real_recorder is not None:
            sys.modules["scriba.recorder"] = self._real_recorder
        else:
            sys.modules.pop("scriba.recorder", None)

    def _folder(self):
        f = Path(tempfile.mkdtemp(prefix="scriba_rec_"))
        # só o stream 'mic' (vira "Eu") — sem loopback, não aciona diarização
        meta = {"status": "recorded", "streams": {"mic": {"file": "mic.wav", "offset_seconds": 0.0}}}
        (f / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        (f / "mic.wav").write_bytes(b"\x00" * 200)  # > 44 bytes p/ passar o guard
        return f

    def test_pipeline_roda_contra_fake_provider(self):
        f = self._folder()
        fake = _FakeProvider([Segment(0.0, 1.5, "ola pessoal"), Segment(1.5, 3.0, "vamos comecar")])
        rc = pipeline.transcribe_folder(f, transcriber=fake)
        self.assertEqual(rc, 0)
        turns = json.loads((f / "transcript.json").read_text(encoding="utf-8"))
        self.assertTrue(turns)
        self.assertTrue(all(t["speaker"] == "Eu" for t in turns))
        texto = " ".join(t["text"] for t in turns)
        self.assertIn("ola pessoal", texto)
        meta = json.loads((f / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "transcribed")
        self.assertEqual(meta["whisper_device"], "cpu")
        self.assertIn("whisper_model", meta)


class WhisperConfigRoundTripTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_wcfg_"))
        util.APP_DIR = d
        util.LOGS_DIR = d / "logs"
        util.CONFIG_PATH = d / "config.toml"

    def test_model_device_round_trip(self):
        import dataclasses
        cfg = config.load()
        novo = dataclasses.replace(cfg, whisper=dataclasses.replace(cfg.whisper, model="small", device="cpu"))
        config.save(novo)
        again = config.load()
        self.assertEqual(again.whisper.model, "small")
        self.assertEqual(again.whisper.device, "cpu")
        self.assertEqual(again.whisper.hotwords, cfg.whisper.hotwords)  # preservado


if __name__ == "__main__":
    unittest.main(verbosity=2)
