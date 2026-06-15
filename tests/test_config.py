"""Testes de scriba.config.load: leitura tolerante a corrupção (#16)."""

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import config, util  # noqa: E402

_TRUNCATED = '[detection]\napps = "isto nao fecha aspas'


class ConfigLoadTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="scriba_cfg_"))
        util.APP_DIR = self.d
        util.LOGS_DIR = self.d / "logs"
        util.CONFIG_PATH = self.d / "config.toml"

    def _seed(self, text):
        util.CONFIG_PATH.write_text(text, encoding="utf-8")

    def _with_apps(self, value):
        return config.DEFAULT_CONFIG.replace('apps = "teams, zoom"', f'apps = "{value}"')

    def test_valido(self):
        self._seed(self._with_apps("webex"))
        self.assertEqual(config.load().detection.apps, "webex")

    def test_corrompido_restaura_do_bak_e_cura(self):
        self._seed(_TRUNCATED)
        util.CONFIG_PATH.with_name("config.toml.bak").write_text(self._with_apps("DOBAK"), encoding="utf-8")
        cfg = config.load()
        self.assertEqual(cfg.detection.apps, "DOBAK")
        with open(util.CONFIG_PATH, "rb") as f:  # arquivo principal foi curado
            self.assertEqual(tomllib.load(f)["detection"]["apps"], "DOBAK")

    def test_corrompido_sem_bak_cai_em_default_com_warning(self):
        self._seed(_TRUNCATED)
        with self.assertLogs("scriba.config", level="WARNING"):
            cfg = config.load()
        self.assertEqual(cfg.detection.apps, "teams, zoom")

    def test_ausente_cria_default(self):
        cfg = config.load()
        self.assertTrue(util.CONFIG_PATH.exists())
        self.assertEqual(cfg.detection.apps, "teams, zoom")


class DiarizationConfigTests(unittest.TestCase):
    """Novos campos de [diarization] (#2): pergunta de nº de participantes."""

    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_diar_"))
        util.APP_DIR = d
        util.LOGS_DIR = d / "logs"
        util.CONFIG_PATH = d / "config.toml"

    def test_defaults(self):
        dz = config.load().diarization
        self.assertTrue(dz.ask_speakers)
        self.assertEqual(dz.ask_speakers_timeout, 90)

    def test_round_trip_preserva_campos(self):
        import dataclasses

        cfg = config.load()
        config.save(dataclasses.replace(
            cfg,
            diarization=dataclasses.replace(
                cfg.diarization, enabled=True, ask_speakers=False,
                ask_speakers_timeout=120, max_speakers=4,
            ),
        ))
        got = config.load().diarization
        self.assertTrue(got.enabled)
        self.assertFalse(got.ask_speakers)
        self.assertEqual(got.ask_speakers_timeout, 120)
        self.assertEqual(got.max_speakers, 4)


_HAVE_DPAPI = util.dpapi_encrypt("probe") is not None


class DpapiKeyTests(unittest.TestCase):
    """Chaves de API cifradas com DPAPI no disco (#12), transparentes em memória."""

    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_dpapi_"))
        util.APP_DIR = d
        util.LOGS_DIR = d / "logs"
        util.CONFIG_PATH = d / "config.toml"

    @unittest.skipUnless(_HAVE_DPAPI, "DPAPI indisponível (não-Windows)")
    def test_dpapi_round_trip(self):
        tok = util.dpapi_encrypt("sk-segredo-123")
        self.assertTrue(tok.startswith("dpapi:"))
        self.assertEqual(util.dpapi_decrypt(tok), "sk-segredo-123")

    def test_decrypt_de_lixo_vira_none(self):
        self.assertIsNone(util.dpapi_decrypt("nao-e-token"))
        self.assertIsNone(util.dpapi_decrypt("dpapi:###naoBase64###"))

    @unittest.skipUnless(_HAVE_DPAPI, "DPAPI indisponível (não-Windows)")
    def test_save_cifra_no_disco_e_load_decifra(self):
        import dataclasses
        cfg = config.load()
        config.save(dataclasses.replace(cfg, summary=dataclasses.replace(cfg.summary, api_key="sk-PLAINTEXT")))
        raw = util.CONFIG_PATH.read_text(encoding="utf-8")
        self.assertNotIn("sk-PLAINTEXT", raw)   # não vaza em texto plano
        self.assertIn("dpapi:", raw)             # gravado cifrado
        self.assertEqual(config.load().summary.api_key, "sk-PLAINTEXT")  # decifra na leitura

    def test_chave_legada_plaintext_carrega(self):
        # config antigo sem marcador (hand-edit) → mantém plaintext (back-compat)
        util.CONFIG_PATH.write_text("[summary]\napi_key = 'plain-legacy'\n", encoding="utf-8")
        self.assertEqual(config.load().summary.api_key, "plain-legacy")


if __name__ == "__main__":
    unittest.main(verbosity=2)
