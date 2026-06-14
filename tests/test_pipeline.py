"""Testes de scriba.pipeline: audio_removed vs no_audio (#20).

Pula automaticamente se as dependências de transcrição (faster-whisper etc.)
não estiverem instaladas — assim a suite roda num checkout enxuto.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import scriba.pipeline as pipeline
    _HAVE_PIPELINE = True
except Exception:  # deps pesadas ausentes
    _HAVE_PIPELINE = False


def _folder(meta, wavs=()):
    d = Path(tempfile.mkdtemp(prefix="scriba_pl_"))
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    for w in wavs:
        (d / w).write_bytes(b"\x00" * 80)
    return d


@unittest.skipUnless(_HAVE_PIPELINE, "scriba.pipeline indisponível (deps de transcrição)")
class AudioRemovedTests(unittest.TestCase):
    def test_archive_audio_keep_false_marca_meta(self):
        d = _folder({"status": "done", "streams": {"mic": {"file": "mic.wav"}}}, wavs=("mic.wav",))
        cfg = SimpleNamespace(audio=SimpleNamespace(keep_audio=False, archive_format="opus"))
        pipeline.archive_audio(d, cfg)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertFalse(list(d.glob("*.wav")))
        self.assertTrue(meta["audio_removed"])
        self.assertIsNone(meta["streams"]["mic"]["file"])

    def test_transcribe_audio_removed_nao_marca_no_audio(self):
        d = _folder({"status": "done", "audio_removed": True, "streams": {"mic": {"file": None}}})
        rc = pipeline.transcribe_folder(d)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(rc, 1)
        self.assertEqual(meta["status"], "done")  # não rebaixou para no_audio


if __name__ == "__main__":
    unittest.main(verbosity=2)
