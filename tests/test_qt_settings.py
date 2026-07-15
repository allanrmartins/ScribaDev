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

    def test_timesheet_tab_dormente_mostra_destaque(self):
        """#126: módulo dormente = cartão de ativação em destaque e aba marcada."""
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        self.assertFalse(win._timesheet_enabled())
        # isHidden = escondido EXPLICITAMENTE (a aba não-corrente esconde o resto)
        self.assertFalse(win._ts_off.isHidden())
        self.assertTrue(win._ts_on.isHidden())
        self.assertIn("✨", win._tabs.tabText(win._ts_tab_index))
        self.assertTrue(win._ts_activate_btn.isEnabled())

    def test_timesheet_tab_ativado_troca_controles(self):
        """#126: com enabled=true o destaque some e dá lugar aos controles normais."""
        import dataclasses

        from scriba import config as config_mod
        from scriba.qt.settings_ui import SettingsWindow

        app = self._app()
        win = SettingsWindow(app)
        cfg = config_mod.load()
        config_mod.save(dataclasses.replace(cfg, timesheet=dataclasses.replace(
            cfg.timesheet, enabled=True)))
        app.reload_config()
        win._sync_timesheet_tab()
        self.assertFalse(win._ts_on.isHidden())
        self.assertTrue(win._ts_off.isHidden())
        self.assertEqual(win._tabs.tabText(win._ts_tab_index), "Apontamento")

    def test_timesheet_ajustes_round_trip(self):
        """#125 (fase 2): a seção [timesheet] editável pela UI; salvar não perde
        nenhuma outra seção do config.toml (nem o enabled, que não é campo)."""
        from scriba import config as config_mod
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        self._field(win, "timesheet", "suggest")[0].setChecked(False)
        self._field(win, "timesheet", "round_minutes")[0].setValue(5)
        self._field(win, "timesheet", "min_meeting_minutes")[0].setValue(20)
        self._field(win, "timesheet", "default_client")[0].setText("Coruripe")
        self._field(win, "timesheet", "export_dir")[0].setText(r"C:\temp\apontamentos")
        win._save()
        r = config_mod.load()
        self.assertFalse(r.timesheet.suggest)
        self.assertEqual(r.timesheet.round_minutes, 5)
        self.assertEqual(r.timesheet.min_meeting_minutes, 20)
        self.assertEqual(r.timesheet.default_client, "Coruripe")
        self.assertEqual(r.timesheet.export_dir, r"C:\temp\apontamentos")
        self.assertFalse(r.timesheet.enabled)              # ativação não é campo
        self.assertEqual(r.whisper.model, "large-v3-turbo")  # outras seções intactas
        self.assertEqual(r.whisper.language, "pt")
        # db_path fica DELIBERADAMENTE fora da UI (OneDrive corrompe o banco)
        with self.assertRaises(KeyError):
            self._field(win, "timesheet", "db_path")

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

    def test_aba_aparencia_tem_grade_de_temas(self):
        from scriba.qt import theme
        from scriba.qt.settings_ui import SettingsWindow, _ThemeCard, _AutoOption

        win = SettingsWindow(self._app())
        grid = win._theme_grid
        items = [grid.itemAt(i).widget() for i in range(grid.count())]
        self.assertEqual(len(items), 1 + len(theme.themes()))       # Automático + os 4 temas
        self.assertIsInstance(items[0], _AutoOption)                # 1ª opção = Automático
        self.assertIsNone(items[0]._value)
        cards = items[1:]
        self.assertTrue(all(isinstance(c, _ThemeCard) for c in cards))
        self.assertEqual([c._value for c in cards], [t.name for t in theme.themes()])

    def test_restyle_reconstroi_a_grade_sem_erro(self):
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        n = win._theme_grid.count()
        win.restyle_theme()                                          # troca a quente (#70)
        self.assertEqual(win._theme_grid.count(), n)                 # reconstruiu, mesma contagem

    def test_token_hf_tem_botao_de_olho(self):
        """#71: o campo Token Hugging Face (e demais campos secretos) tem um botão de
        mostrar/ocultar que alterna o echoMode entre Password e Normal."""
        from PySide6.QtWidgets import QLineEdit, QToolButton
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        field, _ = self._field(win, "diarization", "hf_token")
        self.assertEqual(field.echoMode(), QLineEdit.Password)     # começa oculto
        btn = field.parentWidget().findChild(QToolButton)          # o olho, no container
        self.assertIsNotNone(btn)
        btn.click()
        self.assertEqual(field.echoMode(), QLineEdit.Normal)       # mostra
        btn.click()
        self.assertEqual(field.echoMode(), QLineEdit.Password)     # oculta de novo

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
        raw = util.CONFIG_PATH.read_text(encoding="utf-8")
        if sys.platform == "win32":
            # no disco fica cifrado (dpapi:...) e não vaza em texto plano
            self.assertNotIn("hf_segredo123", raw)
        # POSIX (por ora): DPAPI indisponível -> plaintext, comportamento documentado
        # (secrets nativos = marco futuro do #104); o round-trip vale em qualquer SO
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
