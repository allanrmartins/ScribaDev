"""Testes de scriba.config.load: leitura tolerante a corrupção (#16)."""

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import config, util  # noqa: E402

_TRUNCATED = '[detection]\napps = "isto nao fecha aspas'


class OutputConfigTests(unittest.TestCase):
    def setUp(self):
        self._app0 = util.APP_DIR

    def tearDown(self):
        util.APP_DIR = self._app0

    def test_export_dir_default_e_local_em_appdir(self):
        # default (export_dir vazio) = %LOCALAPPDATA%\ScribaDev\Notas, fora do OneDrive
        util.APP_DIR = Path(r"C:\Users\X\AppData\Local\ScribaDev")
        self.assertEqual(config.Output(export_dir="").resolved_export_dir(),
                         util.APP_DIR / "Notas")

    def test_export_dir_explicito_vence(self):
        self.assertEqual(config.Output(export_dir=r"D:\minhas notas").resolved_export_dir(),
                         Path(r"D:\minhas notas"))


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
        self.assertEqual(dz.chunk_minutes, 3)

    def test_round_trip_preserva_campos(self):
        import dataclasses

        cfg = config.load()
        config.save(dataclasses.replace(
            cfg,
            diarization=dataclasses.replace(
                cfg.diarization, enabled=True, ask_speakers=False,
                ask_speakers_timeout=120, max_speakers=4, chunk_minutes=5,
            ),
        ))
        got = config.load().diarization
        self.assertTrue(got.enabled)
        self.assertFalse(got.ask_speakers)
        self.assertEqual(got.ask_speakers_timeout, 120)
        self.assertEqual(got.max_speakers, 4)
        self.assertEqual(got.chunk_minutes, 5)


class UiConfigTests(unittest.TestCase):
    """Campos de [ui], incluindo o recorte de pendências da capa (#79)."""

    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_ui_"))
        util.APP_DIR = d
        util.LOGS_DIR = d / "logs"
        util.CONFIG_PATH = d / "config.toml"

    def test_default_pending_window_days(self):
        self.assertEqual(config.load().ui.pending_window_days, 30)

    def test_round_trip_pending_window_days(self):
        import dataclasses

        cfg = config.load()
        config.save(dataclasses.replace(cfg, ui=dataclasses.replace(
            cfg.ui, pending_window_days=7)))
        self.assertEqual(config.load().ui.pending_window_days, 7)
        # 0 = sem recorte (escape hatch) também persiste
        cfg = config.load()
        config.save(dataclasses.replace(cfg, ui=dataclasses.replace(
            cfg.ui, pending_window_days=0)))
        self.assertEqual(config.load().ui.pending_window_days, 0)


class TimesheetConfigTests(unittest.TestCase):
    """Seção [timesheet] (#119): módulo de apontamento DORMENTE por padrão (#126)."""

    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_ts_"))
        util.APP_DIR = d
        util.LOGS_DIR = d / "logs"
        util.CONFIG_PATH = d / "config.toml"

    def test_default_dormente(self):
        ts = config.load().timesheet
        self.assertFalse(ts.enabled)  # dormência: update entrega o código desligado
        self.assertTrue(ts.suggest)
        self.assertEqual(ts.round_minutes, 15)
        self.assertEqual(ts.min_meeting_minutes, 10)
        self.assertEqual(ts.default_client, "")
        self.assertEqual(ts.db_path, "")

    def test_round_trip_preserva_campos(self):
        # prova que o template do save() TEM a seção — esquecê-lo perderia a
        # config do timesheet silenciosamente ao salvar pelas Configurações
        import dataclasses

        cfg = config.load()
        config.save(dataclasses.replace(cfg, timesheet=dataclasses.replace(
            cfg.timesheet, enabled=True, round_minutes=5, default_client="Abaco",
            backup_dir=r"D:\backups")))
        got = config.load().timesheet
        self.assertTrue(got.enabled)
        self.assertEqual(got.round_minutes, 5)
        self.assertEqual(got.default_client, "Abaco")
        self.assertEqual(got.backup_dir, r"D:\backups")

    def test_config_antigo_sem_secao_usa_defaults(self):
        # update em máquina existente: config.toml antigo (sem [timesheet]) carrega
        # com o módulo dormente, sem erro
        old = config.DEFAULT_CONFIG.split("[timesheet]")[0]
        util.CONFIG_PATH.write_text(old, encoding="utf-8")
        ts = config.load().timesheet
        self.assertFalse(ts.enabled)
        self.assertEqual(ts.round_minutes, 15)


class WhisperConfigTests(unittest.TestCase):
    """Novos campos de [whisper] (#6): beam_size, cpu_threads, vad, batch_size (#7: default 4)."""

    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_whisper_"))
        util.APP_DIR = d
        util.LOGS_DIR = d / "logs"
        util.CONFIG_PATH = d / "config.toml"

    def test_defaults(self):
        w = config.load().whisper
        self.assertEqual(w.batch_size, 4)
        self.assertEqual(w.beam_size, 3)
        self.assertEqual(w.cpu_threads, 0)
        self.assertEqual(w.vad_min_silence_ms, 0)
        self.assertEqual(w.vad_threshold, 0.0)

    def test_round_trip_preserva_campos(self):
        import dataclasses

        cfg = config.load()
        config.save(dataclasses.replace(cfg, whisper=dataclasses.replace(
            cfg.whisper, batch_size=24, beam_size=1, cpu_threads=8,
            vad_min_silence_ms=500, vad_threshold=0.35)))
        w = config.load().whisper
        self.assertEqual((w.batch_size, w.beam_size, w.cpu_threads), (24, 1, 8))
        self.assertEqual(w.vad_min_silence_ms, 500)
        self.assertAlmostEqual(float(w.vad_threshold), 0.35)


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
        config.save(dataclasses.replace(cfg, summary=dataclasses.replace(cfg.summary, openai_api_key="sk-PLAINTEXT")))
        raw = util.CONFIG_PATH.read_text(encoding="utf-8")
        self.assertNotIn("sk-PLAINTEXT", raw)   # não vaza em texto plano
        self.assertIn("dpapi:", raw)             # gravado cifrado
        self.assertEqual(config.load().summary.openai_api_key, "sk-PLAINTEXT")  # decifra na leitura

    def test_chave_legada_plaintext_carrega(self):
        # config antigo sem marcador (hand-edit) → mantém plaintext (back-compat)
        util.CONFIG_PATH.write_text("[summary]\napi_key = 'plain-legacy'\n", encoding="utf-8")
        self.assertEqual(config.load().summary.api_key, "plain-legacy")


if __name__ == "__main__":
    unittest.main(verbosity=2)
