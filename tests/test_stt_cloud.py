"""Testes do provider de transcrição na nuvem (#23). Sem rede e sem ffmpeg reais:
monkeypatcha urllib.request.urlopen e stt_cloud._chunk_to_opus."""

import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import config, stt_cloud, util  # noqa: E402
from scriba.config import Whisper  # noqa: E402
from scriba.transcription import TranscriptionProvider, make_transcriber  # noqa: E402


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


class CloudProviderTests(unittest.TestCase):
    def setUp(self):
        self._real_urlopen = urllib.request.urlopen
        self._real_chunk = stt_cloud._chunk_to_opus
        self._real_ff = util.ffmpeg_command
        util.ffmpeg_command = lambda: ["ffmpeg"]
        self.reqs = []
        d = Path(tempfile.mkdtemp(prefix="scriba_chunks_"))
        self.chunks = []
        for i in range(2):  # 2 chunks fake (testa offset entre eles)
            p = d / f"chunk_{i:03d}.opus"
            p.write_bytes(b"OPUSDATA" * 4)
            self.chunks.append(p)
        stt_cloud._chunk_to_opus = lambda wav, out_dir: list(self.chunks)

    def tearDown(self):
        urllib.request.urlopen = self._real_urlopen
        stt_cloud._chunk_to_opus = self._real_chunk
        util.ffmpeg_command = self._real_ff

    def _patch_urlopen(self, payload=None, exc=None):
        def fake(req, timeout=None):
            self.reqs.append(req)
            if exc is not None:
                raise exc
            return _FakeResp(payload)
        urllib.request.urlopen = fake

    def test_url_auth_multipart_e_offset_por_chunk(self):
        self._patch_urlopen({"segments": [{"start": 0.0, "end": 5.0, "text": "ola"}]})
        cfg = Whisper(engine="cloud", cloud_api_key="sk-1", cloud_base_url="",
                      cloud_model="whisper-large-v3-turbo")
        segs = stt_cloud.CloudTranscriptionProvider(cfg).transcribe(Path("x.wav"))
        self.assertEqual(len(self.reqs), 2)  # 1 request por chunk
        r0 = self.reqs[0]
        self.assertEqual(r0.full_url, "https://api.groq.com/openai/v1/audio/transcriptions")
        self.assertEqual(r0.get_header("Authorization"), "Bearer sk-1")
        self.assertTrue(r0.get_header("Content-type").startswith("multipart/form-data; boundary="))
        body = r0.data
        for needle in (b'name="file"', b'name="model"', b"whisper-large-v3-turbo",
                       b'name="response_format"', b"verbose_json"):
            self.assertIn(needle, body)
        self.assertEqual(sorted(s.start for s in segs), [0.0, 600.0])  # chunk 1 deslocado +600
        self.assertEqual(segs[0].text, "ola")

    def test_base_url_customizado_strip_barra(self):
        self._patch_urlopen({"segments": []})
        cfg = Whisper(engine="cloud", cloud_api_key="k", cloud_base_url="https://api.x/v1/")
        stt_cloud.CloudTranscriptionProvider(cfg).transcribe(Path("a.wav"))
        self.assertEqual(self.reqs[0].full_url, "https://api.x/v1/audio/transcriptions")

    def test_falha_http_vira_raise(self):
        self._patch_urlopen(exc=urllib.error.URLError("boom"))
        cfg = Whisper(engine="cloud", cloud_api_key="k")
        with self.assertRaises(RuntimeError):
            stt_cloud.CloudTranscriptionProvider(cfg).transcribe(Path("a.wav"))

    def test_ensure_loaded_sem_chave_raise(self):
        with self.assertRaises(RuntimeError):
            stt_cloud.CloudTranscriptionProvider(Whisper(engine="cloud", cloud_api_key="")).ensure_loaded()

    def test_ensure_loaded_sem_ffmpeg_raise(self):
        util.ffmpeg_command = lambda: None
        with self.assertRaises(RuntimeError):
            stt_cloud.CloudTranscriptionProvider(Whisper(engine="cloud", cloud_api_key="k")).ensure_loaded()

    def test_conformance_e_factory(self):
        cfg = Whisper(engine="cloud", cloud_api_key="k")
        self.assertIsInstance(stt_cloud.CloudTranscriptionProvider(cfg), TranscriptionProvider)
        self.assertIsInstance(make_transcriber(cfg), stt_cloud.CloudTranscriptionProvider)


class CloudConfigRoundTripTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_ccfg_"))
        util.APP_DIR = d
        util.LOGS_DIR = d / "logs"
        util.CONFIG_PATH = d / "config.toml"

    def test_round_trip_campos_cloud(self):
        import dataclasses
        cfg = config.load()
        novo = dataclasses.replace(cfg, whisper=dataclasses.replace(
            cfg.whisper, engine="cloud", cloud_base_url="https://api.groq.com/openai/v1",
            cloud_api_key="sk-z", cloud_model="whisper-large-v3"))
        config.save(novo)
        w = config.load().whisper
        self.assertEqual(w.engine, "cloud")
        self.assertEqual(w.cloud_base_url, "https://api.groq.com/openai/v1")
        self.assertEqual(w.cloud_api_key, "sk-z")
        self.assertEqual(w.cloud_model, "whisper-large-v3")

    def test_config_antigo_assume_engine_local(self):
        util.CONFIG_PATH.write_text("[whisper]\nmodel = 'small'\n", encoding="utf-8")
        self.assertEqual(config.load().whisper.engine, "local")


if __name__ == "__main__":
    unittest.main(verbosity=2)
