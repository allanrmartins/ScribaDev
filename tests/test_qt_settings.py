"""Configurações Qt (#50, slice 1): smoke + round-trip load/save (integridade da config)."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from scriba import config as config_mod, util

        self._orig = util.CONFIG_PATH
        util.CONFIG_PATH = Path(tempfile.mkdtemp(prefix="scriba_cfg_")) / "config.toml"
        util.CONFIG_PATH.write_text(config_mod.DEFAULT_CONFIG, encoding="utf-8")

    def tearDown(self):
        from scriba import util

        util.CONFIG_PATH = self._orig

    def _app(self):
        from scriba import config as config_mod

        class _App:
            cfg = config_mod.load()

            def reload_config(self):
                self.cfg = config_mod.load()

        return _App()

    def _field(self, win, section, attr):
        for w, s, a, kind, ch in win._fields:
            if s == section and a == attr:
                return w, kind
        raise KeyError((section, attr))

    def test_load_preenche_dos_defaults(self):
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        self.assertTrue(win._loaded)
        model, _ = self._field(win, "whisper", "model")
        self.assertEqual(model.currentText(), "large-v3-turbo")
        auto, _ = self._field(win, "detection", "auto_record")
        self.assertTrue(auto.isChecked())
        arch, _ = self._field(win, "audio", "archive_format")
        self.assertEqual(arch.currentData(), "opus")

    def test_round_trip_persiste_mudancas(self):
        from scriba import config as config_mod
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        self._field(win, "whisper", "model")[0].setCurrentText("small")
        self._field(win, "detection", "auto_record")[0].setChecked(False)
        self._field(win, "audio", "retention_days")[0].setValue(7)
        self._field(win, "summary", "provider")[0].setCurrentIndex(
            self._field(win, "summary", "provider")[0].findData("ollama"))
        win._save()

        reloaded = config_mod.load()
        self.assertEqual(reloaded.whisper.model, "small")
        self.assertFalse(reloaded.detection.auto_record)
        self.assertEqual(reloaded.audio.retention_days, 7)
        self.assertEqual(reloaded.summary.provider, "ollama")
        # campo não tocado permanece
        self.assertEqual(reloaded.whisper.language, "pt")

    def test_secret_round_trip_cifra_decifra(self):
        from scriba import config as config_mod
        from scriba.qt.settings_ui import SettingsWindow

        from scriba import util

        win = SettingsWindow(self._app())
        self._field(win, "diarization", "hf_token")[0].setText("hf_segredo123")
        win._save()
        # no disco fica cifrado (dpapi:...); load() decifra de volta
        raw = util.CONFIG_PATH.read_text(encoding="utf-8")
        self.assertNotIn("hf_segredo123", raw)              # não vaza em texto plano
        self.assertEqual(config_mod.load().diarization.hf_token, "hf_segredo123")

    def test_guard_nao_salva_sem_carregar(self):
        from scriba import config as config_mod
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        self._field(win, "whisper", "model")[0].setCurrentText("tiny")
        win._loaded = False           # simula load incompleto
        win._save()
        self.assertNotEqual(config_mod.load().whisper.model, "tiny")  # config boa preservada


if __name__ == "__main__":
    unittest.main(verbosity=2)
