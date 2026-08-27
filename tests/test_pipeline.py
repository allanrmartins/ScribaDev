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


@unittest.skipUnless(_HAVE_PIPELINE, "scriba.pipeline indisponível (deps de transcrição)")
class MetaDesatualizadoTests(unittest.TestCase):
    """#187: o crash de encoding do exe derrubava o archive_audio DEPOIS do
    transcode e ANTES de apontar o meta - sobrando streams com .wav mortos e o
    .opus vivo do lado. As vítimas precisam se recuperar sozinhas."""

    def test_archive_audio_grava_o_meta_antes_de_qualquer_print(self):
        # mesmo com o print do resultado estourando (era o UnicodeEncodeError
        # do cp1252), o meta já aponta os arquivos novos - os .wav morreram no
        # transcode e o meta não pode mentir sobre eles
        from unittest import mock

        d = _folder({"status": "done", "streams": {"mic": {"file": "mic.wav"}}},
                    wavs=("mic.wav",))
        out = d / "mic.opus"

        def _fake_transcode(_ff, w, _args, _ext):
            out.write_bytes(b"\x00" * 50)
            w.unlink()
            return {"wav": w, "ok": True, "out": out, "before": 80, "after": 50}

        real_print = print

        def _print_explosivo(*args, **kw):
            if args and "->" in str(args[0]):
                raise RuntimeError("stdout cp1252 engasgou")
            real_print(*args, **kw)

        cfg = SimpleNamespace(audio=SimpleNamespace(keep_audio=True, archive_format="opus"))
        with mock.patch.object(pipeline, "_transcode_one", _fake_transcode), \
                mock.patch("scriba.util.ffmpeg_command", return_value=["ffmpeg"]), \
                mock.patch("builtins.print", _print_explosivo):
            with self.assertRaises(RuntimeError):
                pipeline.archive_audio(d, cfg)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["streams"]["mic"]["file"], "mic.opus")
        self.assertEqual(meta["archive_format"], "opus")

    def test_transcribe_resolve_wav_morto_para_opus_e_conserta_o_meta(self):
        # vítima real do #187: meta aponta mic.wav (morto), mic.opus vivo do lado
        from unittest import mock

        from scriba.transcriber import Segment

        d = _folder({"status": "done",
                     "streams": {"mic": {"file": "mic.wav", "offset_seconds": 0.0}}})
        (d / "mic.opus").write_bytes(b"\x00" * 80)

        class _FakeTr:
            device_used = "cpu"

            def ensure_loaded(self):
                return "cpu"

            def transcribe(self, wav):
                assert wav.name == "mic.opus"   # usou o arquivo VIVO
                return [Segment(0.0, 1.0, "olá")]

            def close(self):
                pass

        cfg = SimpleNamespace(whisper=SimpleNamespace(engine="local", model="fake"))
        with mock.patch.object(pipeline, "load", return_value=cfg):
            rc = pipeline.transcribe_folder(d, transcriber=_FakeTr())
        self.assertEqual(rc, 0)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "transcribed")
        self.assertEqual(meta["streams"]["mic"]["file"], "mic.opus")  # ponteiro consertado
        self.assertTrue((d / "transcript.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
