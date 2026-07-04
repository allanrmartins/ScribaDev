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

        d = Path(tempfile.mkdtemp(prefix="scriba_cfg_"))
        self._orig = util.CONFIG_PATH
        self._orig_prompt = util.PROMPT_PATH
        util.CONFIG_PATH = d / "config.toml"
        util.CONFIG_PATH.write_text(config_mod.DEFAULT_CONFIG, encoding="utf-8")
        util.PROMPT_PATH = d / "prompt.md"   # não tocar o prompt.md real

    def tearDown(self):
        from scriba import util

        util.CONFIG_PATH = self._orig
        util.PROMPT_PATH = self._orig_prompt

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

    def test_aba_aparencia_tem_seletor_de_tema(self):
        from scriba.qt import theme
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        combo = win._theme_combo
        self.assertEqual(combo.count(), 1 + len(theme.themes()))   # Automático + os 4 temas
        self.assertIsNone(combo.itemData(0))                        # 1º item = Automático (None)
        slugs = [combo.itemData(i) for i in range(1, combo.count())]
        self.assertEqual(slugs, [t.name for t in theme.themes()])

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

    def test_device_combo_round_trip(self):
        from scriba import config as config_mod
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        mic, kind = self._field(win, "audio", "mic_device")
        self.assertEqual(kind, "device")
        self.assertEqual(win._widget_get(mic, "device", None), "")   # '(padrão)' selecionado -> ''
        win._fill_devices({"mics": ["FIFINE Microphone", "Headset"], "loopbacks": []})
        mic.setCurrentIndex(mic.findText("FIFINE Microphone"))
        win._save()
        self.assertEqual(config_mod.load().audio.mic_device, "FIFINE Microphone")

    def test_device_combo_passthrough_aparelho_desconectado(self):
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        mic, _ = self._field(win, "audio", "mic_device")
        # valor salvo que não está na lista enumerada: mostrado como texto e devolvido no get
        win._widget_set(mic, "device", None, "Aparelho Desconectado")
        self.assertEqual(mic.currentText(), "Aparelho Desconectado")
        self.assertEqual(win._widget_get(mic, "device", None), "Aparelho Desconectado")

    def test_dropdowns_nao_sao_editaveis(self):
        # regra do Allan: todo dropdown abre ao clicar e NÃO deixa digitar (como o Motor).
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        for sec, attr in (("whisper", "engine"), ("whisper", "model"), ("summary", "model"),
                          ("summary", "chat_model"), ("audio", "mic_device"),
                          ("audio", "loopback_device")):
            w, _ = self._field(win, sec, attr)
            self.assertFalse(w.isEditable(), f"{sec}.{attr} deveria ser dropdown puro (não editável)")

    def test_whisper_model_passthrough_exibe_valor_fora_da_lista(self):
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        model, _ = self._field(win, "whisper", "model")
        win._widget_set(model, "editable_text", None, "meu-modelo-custom")   # valor salvo fora dos presets
        self.assertEqual(model.currentText(), "meu-modelo-custom")           # exibido, sem precisar digitar
        self.assertEqual(win._widget_get(model, "editable_text", None), "meu-modelo-custom")

    def test_hotwords_bigtext_normaliza_whitespace(self):
        from scriba import config as config_mod
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        hw, kind = self._field(win, "whisper", "hotwords")
        self.assertEqual(kind, "bigtext")                 # virou campo multi-linha
        hw.setPlainText("SAP  ABAP\nBAPI\n\nCDS")         # linhas / espaços múltiplos
        win._save()
        self.assertEqual(config_mod.load().whisper.hotwords, "SAP ABAP BAPI CDS")  # normalizado

    def test_prompt_editor_carrega_e_salva(self):
        from scriba import util
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        self.assertTrue(win._prompt_editor.toPlainText().strip())   # carregou o prompt.md
        win._prompt_editor.setPlainText("Resuma em bullets curtos.")
        win._save()
        self.assertIn("bullets curtos", util.PROMPT_PATH.read_text(encoding="utf-8"))

    def test_restaurar_padrao(self):
        from scriba import notes
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        win._prompt_editor.setPlainText("qualquer coisa")
        win._restore_prompt()
        self.assertEqual(win._prompt_editor.toPlainText(), notes.DEFAULT_SUMMARY_PROMPT)

    def test_aba_sobre_e_componentes(self):
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        titles = [win._tabs.tabText(i) for i in range(win._tabs.count())]
        self.assertIn("Sobre", titles)
        comps = win._about_components()
        self.assertTrue(any(label == "Python" for label, _v, _l in comps))

    def test_show_about_update(self):
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        win._show_about_update(None)
        self.assertIn("Não consegui", win._about_status.text())
        win._show_about_update("99.9.9")                        # versão futura -> há update
        self.assertFalse(win._about_update_btn.isHidden())      # botão marcado visível


if __name__ == "__main__":
    unittest.main(verbosity=2)
