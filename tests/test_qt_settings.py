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
        self._orig_context = util.CONTEXT_PATH
        self._orig_state = util.STATE_PATH
        util.CONFIG_PATH = d / "config.toml"
        util.CONFIG_PATH.write_text(config_mod.DEFAULT_CONFIG, encoding="utf-8")
        util.PROMPT_PATH = d / "prompt.md"     # não tocar o prompt.md real
        util.CONTEXT_PATH = d / "context.md"   # nem o context.md, que o editor grava
        util.STATE_PATH = d / "state.json"

    def tearDown(self):
        from scriba import util

        util.CONFIG_PATH = self._orig
        util.PROMPT_PATH = self._orig_prompt
        util.CONTEXT_PATH = self._orig_context
        util.STATE_PATH = self._orig_state

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
        self._field(win, "timesheet", "default_client")[0].setText("Vetra")
        self._field(win, "timesheet", "export_dir")[0].setText(r"C:\temp\apontamentos")
        win._save()
        r = config_mod.load()
        self.assertFalse(r.timesheet.suggest)
        self.assertEqual(r.timesheet.round_minutes, 5)
        self.assertEqual(r.timesheet.min_meeting_minutes, 20)
        self.assertEqual(r.timesheet.default_client, "Vetra")
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

    def test_context_editor_carrega_e_salva(self):
        """O cabeçalho "Contexto para IA" era o único texto sem edição na interface
        (#181): só o assistente de perfil escrevia o context.md."""
        from scriba import notes, util
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        self.assertEqual(win._context_editor.toPlainText(), "")   # opt-in: nasce vazio
        self.assertFalse(util.CONTEXT_PATH.exists(), "abrir a janela não pode criar arquivo")
        win._context_editor.setPlainText("> **Contexto para IA:** ata de reuniao juridica.")
        win._save()
        self.assertIn("juridica", util.CONTEXT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(notes.load_context_note(),
                         "> **Contexto para IA:** ata de reuniao juridica.")

    def test_caso_do_relato_trocar_o_cabecalho_sem_refazer_o_perfil(self):
        """#181 nasceu de quem não é da área SAP e levava "reunião SAP/ABAP" em toda
        ata. Essa pessoa JÁ tem o app instalado, então a migração preserva o texto
        antigo - e ela precisa conseguir trocar ou tirar só esse trecho aqui, sem
        refazer o perfil inteiro no assistente."""
        from scriba import notes, util
        from scriba.qt.settings_ui import SettingsWindow

        notes.freeze_area_defaults()   # instalação que já existia: chega com o texto SAP
        self.assertIn("SAP/ABAP", notes.load_context_note())

        win = SettingsWindow(self._app())
        self.assertIn("SAP/ABAP", win._context_editor.toPlainText())

        win._restore_context()         # um clique: texto sugerido, sem jargão de área
        win._save()
        self.assertNotIn("SAP", notes.load_context_note())
        self.assertIn("Contexto para IA", notes.load_context_note())

        win._context_editor.setPlainText("")   # ou simplesmente tirar o cabeçalho
        win._save()
        self.assertEqual(notes.load_context_note(), "")
        # e nada disso mexeu no prompt do resumo: o perfil dela segue como estava
        self.assertEqual(util.PROMPT_PATH.read_text(encoding="utf-8").strip(),
                         notes.DEFAULT_SUMMARY_PROMPT.strip())

    def test_botao_preenche_com_o_texto_sugerido(self):
        from scriba import notes
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        win._restore_context()
        self.assertEqual(win._context_editor.toPlainText(), notes.suggested_context_note())
        self.assertNotIn("SAP", win._context_editor.toPlainText())

    def test_context_editor_vazio_tira_o_cabecalho_da_nota(self):
        from scriba import notes
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        win._context_editor.setPlainText("")
        win._save()
        self.assertEqual(notes.load_context_note(), "")

    def test_restaurar_padrao(self):
        """"Padrão" é o que uma instalação nova recebe: o genérico, não o SAP/ABAP
        (#181). Quem quer o texto de uma área volta por "Assistente de perfil…",
        que fica no mesmo grupo, ao lado deste botão."""
        from scriba import notes
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        win._prompt_editor.setPlainText("qualquer coisa")
        win._restore_prompt()
        self.assertEqual(win._prompt_editor.toPlainText(), notes.default_summary_prompt())
        self.assertNotIn("SAP/ABAP", win._prompt_editor.toPlainText())

    def test_aba_sobre_e_componentes(self):
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        titles = [win._tabs.tabText(i) for i in range(win._tabs.count())]
        self.assertIn("Sobre", titles)
        comps = win._about_components()
        self.assertTrue(any(label == "Python" for label, _v, _l in comps))

    def test_pkg_version_honesta_sem_metadata(self):
        """Report de usuário (instalador): o bundle PyInstaller não traz o dist-info,
        então metadata ausente com o MÓDULO presente não pode virar 'AUSENTE
        (reinstale o app)' — cai p/ find_spec e responde 'presente'."""
        from scriba.qt.settings_ui import _pkg_version

        self.assertEqual(_pkg_version("dist-que-nao-existe", "json"), "presente")
        self.assertIsNone(_pkg_version("dist-que-nao-existe", "modulo_inexistente_xyz"))
        self.assertNotIn(_pkg_version("PySide6"), (None, "presente"))  # metadata real

    def test_diar_health_ausencia_nao_e_erro_ao_checar(self):
        """find_spec('pyannote.audio') LEVANTA ModuleNotFoundError quando nem o
        namespace 'pyannote' existe — é 'não instalada', nunca 'erro ao checar'."""
        from unittest import mock

        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        with mock.patch("importlib.util.find_spec",
                        side_effect=ModuleNotFoundError("No module named 'pyannote'")):
            text, level = win._diar_health(lambda *_a, **_k: None)
        self.assertEqual(level, "err")
        self.assertIn("não instalada", text)
        self.assertNotIn("erro ao checar", text)

    # -- seção "Baixar componentes" (#154, só instalação congelada) -----------
    def test_secao_componentes_so_na_instalacao_congelada(self):
        from unittest import mock

        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        self.assertFalse(hasattr(win, "_comp_btn"))      # git/venv: extras via pip, sem seção
        with mock.patch("scriba.updates.is_frozen_install", return_value=True):
            win2 = SettingsWindow(self._app())
        self.assertTrue(hasattr(win2, "_comp_btn"))

    def test_baixar_componentes_monta_o_plano_e_conclui(self):
        from unittest import mock

        from scriba import components
        from scriba.qt.settings_ui import SettingsWindow

        with mock.patch("scriba.updates.is_frozen_install", return_value=True):
            win = SettingsWindow(self._app())
        win._inline_bg = True
        win._comp_model.setChecked(False)
        win._comp_cuda.setChecked(False)
        win._comp_voices.setChecked(True)
        with mock.patch.object(components, "run", return_value=(True, "")) as run:
            win._download_components()
        self.assertEqual([k for k, _l, _mb in run.call_args[0][0]], ["voices"])
        self.assertIn("Pronto", win._comp_status.text())
        self.assertTrue(win._comp_btn.isEnabled())

    def test_baixar_componentes_falha_reabilita_e_avisa(self):
        from unittest import mock

        from scriba import components
        from scriba.qt.settings_ui import SettingsWindow

        with mock.patch("scriba.updates.is_frozen_install", return_value=True):
            win = SettingsWindow(self._app())
        win._inline_bg = True
        win._comp_voices.setChecked(True)
        self.assertTrue(win._comp_report_btn.isHidden())  # sem erro, sem botão de reporte
        with mock.patch.object(components, "run", return_value=(False, "componentes: pip 1")):
            win._download_components()
        self.assertIn("falharam", win._comp_status.text())
        self.assertTrue(win._comp_btn.isEnabled())       # re-tentável
        self.assertFalse(win._comp_report_btn.isHidden())  # erro → oferece reportar

    def test_reportar_erro_de_componentes_abre_issue(self):
        from unittest import mock

        from scriba.qt.settings_ui import SettingsWindow

        with mock.patch("scriba.updates.is_frozen_install", return_value=True):
            win = SettingsWindow(self._app())
        win._comp_last_notes = "componentes: pip retornou 2"
        fake_zip = Path(tempfile.mkdtemp(prefix="scriba_rep_")) / "diag.zip"
        fake_zip.write_bytes(b"z")
        with mock.patch("scriba.support.open_report", return_value=fake_zip) as rep:
            win._report_components_error()
        self.assertIn("pip retornou 2", rep.call_args[0][1])   # detalhe vai p/ a issue
        self.assertIn("diag.zip", win._comp_status.text())     # instrução de arraste

    def test_baixar_componentes_nada_selecionado(self):
        from unittest import mock

        from scriba.qt.settings_ui import SettingsWindow

        with mock.patch("scriba.updates.is_frozen_install", return_value=True):
            win = SettingsWindow(self._app())
        for chk in (win._comp_model, win._comp_cuda, win._comp_voices):
            chk.setChecked(False)
        win._download_components()
        self.assertIn("Nada selecionado", win._comp_status.text())

    def _win_componentes(self):
        from unittest import mock

        from scriba.qt.settings_ui import SettingsWindow

        with mock.patch("scriba.updates.is_frozen_install", return_value=True):
            return SettingsWindow(self._app())

    def test_reinstalar_recusa_com_reuniao_em_processamento(self):
        """#176: apagar o addons no meio de uma transcrição mata a transcrição -
        foi assim que a pasta do usuário sumiu debaixo do subprocesso."""
        from unittest import mock

        win = self._win_componentes()
        with mock.patch.object(win, "_reuniao_em_processamento", return_value="10-00_Call"), \
                mock.patch("scriba.addons.reset_addons") as reset:
            win._repair_components()
        reset.assert_not_called()
        self.assertIn("10-00_Call", win._comp_status.text())

    def test_reinstalar_apaga_e_ja_baixa_de_novo(self):
        """O pedido do 'desinstalar e baixar de novo': o que estava instalado é
        medido ANTES de apagar e volta a ser baixado sem o usuário remarcar nada."""
        from unittest import mock

        from PySide6.QtWidgets import QMessageBox

        addons_dir = Path(tempfile.mkdtemp(prefix="scriba_add_"))
        (addons_dir / "nvidia_cublas_cu12").mkdir()
        win = self._win_componentes()
        with mock.patch.object(win, "_reuniao_em_processamento", return_value=None), \
                mock.patch("scriba.addons.is_installing", return_value=False), \
                mock.patch("scriba.addons.addons_dir", return_value=addons_dir), \
                mock.patch("scriba.addons.reset_addons", return_value=(True, "componentes removidos")), \
                mock.patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.Yes), \
                mock.patch.object(win, "_download_components") as baixar:
            win._repair_components()
        baixar.assert_called_once()
        self.assertTrue(win._comp_cuda.isChecked())      # tinha CUDA -> volta a baixar
        self.assertFalse(win._comp_model.isChecked())    # cache do modelo não é apagado

    def test_arquivo_em_uso_oferece_agendar_para_o_proximo_inicio(self):
        """#176: quando o SO segura os arquivos, o caminho deixa de ser 'vire-se no
        Explorer' e passa a ser um agendamento que o boot seguinte cumpre."""
        from unittest import mock

        from PySide6.QtWidgets import QMessageBox

        win = self._win_componentes()
        with mock.patch.object(win, "_reuniao_em_processamento", return_value=None), \
                mock.patch("scriba.addons.is_installing", return_value=False), \
                mock.patch("scriba.addons.reset_addons",
                           return_value=(False, "algum processo ainda está usando esses arquivos")), \
                mock.patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.Yes), \
                mock.patch("scriba.addons.schedule_reset", return_value=True) as agendar:
            win._repair_components()
        agendar.assert_called_once()
        self.assertIn("próximo início", win._comp_status.text())

    def test_show_about_update(self):
        from scriba.qt.settings_ui import SettingsWindow

        win = SettingsWindow(self._app())
        win._show_about_update(None)
        self.assertIn("Não consegui", win._about_status.text())
        win._show_about_update("99.9.9")                        # versão futura -> há update
        self.assertFalse(win._about_update_btn.isHidden())      # botão marcado visível


if __name__ == "__main__":
    unittest.main(verbosity=2)
