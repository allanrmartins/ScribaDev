"""Log e Wizard Qt (#51): smoke offscreen (render, nível, busca, crash / prévia, jargão)."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None


class _App:
    cfg = type("C", (), {"output": type("O", (), {
        "resolved_recordings_dir": staticmethod(lambda: None)})()})()


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class LogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_render_e_status(self):
        from scriba.qt.log_ui import LogWindow

        win = LogWindow(_App())
        win._render()
        self.assertIn("entradas", win._status.text())

    def test_restyle_theme_smoke(self):
        from scriba.qt import theme
        from scriba.qt.log_ui import LogWindow

        win = LogWindow(_App())
        orig = theme._active
        self.addCleanup(lambda: setattr(theme, "_active", orig))
        theme._active = theme.by_slug("light")
        win.restyle_theme()   # troca a quente (#70): re-aplica view/status + re-render
        self.assertIn(theme.by_slug("light").code_bg.lower(), win._view.styleSheet().lower())

    def test_cycle_level(self):
        from scriba.qt.log_ui import LogWindow

        win = LogWindow(_App())
        self.assertEqual(win._level_idx, 0)
        win._cycle_level()
        self.assertEqual(win._level_idx, 1)
        self.assertIn("Avisos", win._level_btn.text())

    def test_busca_conta_ocorrencias(self):
        from scriba.qt.log_ui import LogWindow

        win = LogWindow(_App())
        win._view.setPlainText("linha um erro\nlinha dois\nerro de novo")
        win._find.setText("erro")
        win._apply_search()
        self.assertGreaterEqual(len(win._hits), 2)
        self.assertIn("/", win._count.text())

    def test_auto_follow_tick_segue_o_fim(self):
        from scriba.qt.log_ui import LogWindow

        win = LogWindow(_App())
        calls = []
        win._reload = lambda stick_bottom=False: calls.append(stick_bottom)
        win.isVisible = lambda: True
        win._stat = 0
        win._log_stat = lambda: win._stat        # controla a detecção de mudança
        win._last_stat = None
        win._auto.setChecked(True)
        win._find.setText("")
        win._tick()
        self.assertEqual(calls, [True])              # auto marcado + arquivo mudou: recarrega no fim
        calls.clear()
        win._find.setText("erro")                    # busca ativa: pausa (posições estáveis)
        win._tick()
        self.assertEqual(calls, [])
        calls.clear()
        win._find.setText("")
        win._auto.setChecked(False)                  # auto desmarcado: scroll livre
        win._tick()
        self.assertEqual(calls, [])

    def test_tick_pula_reload_se_log_nao_mudou(self):
        # #93: sem mudança no arquivo, o tick NÃO relê (não reconstrói setHtml idêntico
        # nem apaga seleção/scroll a cada 2s)
        from scriba.qt.log_ui import LogWindow

        win = LogWindow(_App())
        calls = []
        win._reload = lambda stick_bottom=False: calls.append(stick_bottom)
        win.isVisible = lambda: True
        win._auto.setChecked(True)
        win._find.setText("")
        win._log_stat = lambda: (111.0, 222)
        win._last_stat = (111.0, 222)                # igual: nada a fazer
        win._tick()
        self.assertEqual(calls, [])
        win._last_stat = (111.0, 999)                # tamanho mudou: relê
        win._tick()
        self.assertEqual(calls, [True])

    def test_tick_pausa_com_selecao_ativa(self):
        # #93: enquanto o usuário seleciona texto p/ copiar, o tick não puxa o tapete
        from scriba.qt.log_ui import LogWindow
        from PySide6.QtGui import QTextCursor

        win = LogWindow(_App())
        calls = []
        win._reload = lambda stick_bottom=False: calls.append(stick_bottom)
        win.isVisible = lambda: True
        win._auto.setChecked(True)
        win._find.setText("")
        win._log_stat = lambda: object()             # sempre "mudou"
        win._last_stat = None
        win._view.setPlainText("linha um\nlinha dois\nlinha tres")
        cur = win._view.textCursor()
        cur.select(QTextCursor.Document)             # seleção ativa
        win._view.setTextCursor(cur)
        self.assertTrue(win._view.textCursor().hasSelection())
        win._tick()
        self.assertEqual(calls, [])                  # pausou por causa da seleção
        win._view.setPlainText("x")                  # limpa a seleção
        win._tick()
        self.assertEqual(calls, [True])

    def test_show_nao_gruda_no_fim_com_busca_ativa(self):
        # #93: reabrir com um termo de busca persistido não pode grudar no fim (jogaria
        # o 1º hit p/ fora da tela). O campo persiste porque closeEvent só esconde.
        from scriba.qt.log_ui import LogWindow

        win = LogWindow(_App())
        sticks = []
        win._reload = lambda stick_bottom=False: sticks.append(stick_bottom)
        win._auto.setChecked(True)
        win._find.setText("erro")
        win.show()
        self.assertEqual(sticks, [False])            # busca ativa: não grudou no fim
        win.close()
        sticks.clear()
        win._find.setText("")
        win.show()
        self.assertEqual(sticks, [True])             # sem busca + auto: grudou no fim
        win.close()

    def test_render_stick_bottom_move_cursor_ao_fim(self):
        from scriba import diagnostics
        from scriba.qt.log_ui import LogWindow

        win = LogWindow(_App())
        text = "\n".join(f"06/07/2026 10:00:{i % 60:02d} INFO scriba: linha {i}" for i in range(60))
        win._entries = diagnostics.parse_entries(text)
        win._render(stick_bottom=True)
        self.assertTrue(win._view.textCursor().atEnd())    # rolou p/ o fim (tail)
        win._render(stick_bottom=False)                    # setHtml volta o cursor ao início
        self.assertFalse(win._view.textCursor().atEnd())

    def test_crash_dialog_singleton(self):
        from scriba.qt import log_ui

        log_ui._reset_crash()
        log_ui.show_crash_dialog(_App(), "Traceback (most recent call last): ...")
        self.assertIsNotNone(log_ui._crash_win)
        log_ui.show_crash_dialog(_App(), "outro")   # não empilha
        log_ui._reset_crash()
        self.assertIsNone(log_ui._crash_win)


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class WizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_modelo_pronto_preenche_previa(self):
        from scriba.qt.wizard_ui import WizardWindow

        win = WizardWindow()
        win._role.setText("Gerente de projetos")
        win._generate_template()                       # promptgen.template_prompt (puro)
        self.assertIsNotNone(win._result)
        self.assertTrue(win._preview.toPlainText())
        self.assertIn("Prévia pronta", win._status.text())

    def test_apply_sem_previa_avisa(self):
        from scriba.qt.wizard_ui import WizardWindow

        win = WizardWindow()
        win._result = None
        win._apply()
        self.assertIn("Gere uma prévia", win._status.text())

    def test_on_prompt_none_mostra_erro(self):
        from scriba.qt.wizard_ui import WizardWindow

        win = WizardWindow()
        win._on_prompt(None)
        self.assertIn("Não consegui gerar", win._status.text())

    def test_gerar_com_ia_avisa_cli_ausente(self):
        """Report de usuário (instalador sem claude CLI): 'Gerar com IA' avisa NA HORA
        que a CLI não existe — não falha mudo depois de um minuto de espera."""
        from unittest import mock

        from scriba.qt.wizard_ui import WizardWindow

        win = WizardWindow()
        with mock.patch("scriba.util.claude_command", return_value=None):
            win._generate_ai()
        self.assertIn("não foi encontrada", win._status.text())
        self.assertFalse(win._busy)   # nem entrou em "Gerando…"

    def test_sugerir_jargao_avisa_cli_ausente(self):
        from unittest import mock

        from scriba.qt.wizard_ui import WizardWindow

        win = WizardWindow()
        with mock.patch("scriba.util.claude_command", return_value=None):
            win._suggest_jargon()
        self.assertIn("não foi encontrada", win._status.text())
        self.assertFalse(win._busy)

    def test_falha_com_cli_deslogada_da_receita_de_login(self):
        from scriba import ai
        from scriba.qt.wizard_ui import WizardWindow

        win = WizardWindow()
        ai.last_error = ai.ERR_LOGGED_OUT
        self.addCleanup(lambda: setattr(ai, "last_error", None))
        win._on_prompt(None)
        self.assertIn("/login", win._status.text())
        win._on_jargon(None)
        self.assertIn("/login", win._status.text())

    def test_on_jargon_mescla(self):
        from scriba.qt.wizard_ui import WizardWindow

        win = WizardWindow()
        win._jargon.setPlainText("SAP")
        win._on_jargon("SAP, RAP, CDS")               # não duplica SAP
        merged = win._jargon.toPlainText()
        self.assertIn("RAP", merged)
        self.assertEqual(merged.lower().count("sap"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class WizardTimesheetTests(unittest.TestCase):
    """#118/#126: opt-in do apontamento de horas no onboarding (config isolado)."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import tempfile

        from scriba import config as config_mod, util

        d = Path(tempfile.mkdtemp(prefix="scriba_wizts_"))
        self._orig = util.CONFIG_PATH
        util.CONFIG_PATH = d / "config.toml"
        util.CONFIG_PATH.write_text(config_mod.DEFAULT_CONFIG, encoding="utf-8")

    def tearDown(self):
        from scriba import util

        util.CONFIG_PATH = self._orig

    def test_dormente_oferece_opt_in_desmarcado(self):
        from scriba.qt.wizard_ui import WizardWindow

        win = WizardWindow()
        self.assertIsNotNone(win._timesheet_opt)
        self.assertFalse(win._timesheet_opt.isChecked())  # pular = zero side effects

    def test_ja_ativado_nao_oferece(self):
        import dataclasses

        from scriba import config as config_mod
        from scriba.qt.wizard_ui import WizardWindow

        cfg = config_mod.load()
        config_mod.save(dataclasses.replace(cfg, timesheet=dataclasses.replace(
            cfg.timesheet, enabled=True)))
        win = WizardWindow()
        self.assertIsNone(win._timesheet_opt)

    def test_janela_apertada_nao_come_o_texto_do_card(self):
        """Regressão (texto comido, report de usuário): com a janela na altura MÍNIMA,
        os labels com word-wrap (card do apontamento e cabeçalho) recebem a altura que
        o texto pede — o aperto vai p/ a prévia (que tem scroll), nunca corta o texto."""
        from PySide6.QtWidgets import QApplication, QLabel

        from scriba.qt.wizard_ui import WizardWindow

        win = WizardWindow()
        win.resize(win.minimumWidth(), win.minimumHeight())
        win.show()
        self.addCleanup(win.close)
        QApplication.processEvents()
        wrapped = [lb for lb in win.findChildren(QLabel)
                   if lb.wordWrap() and lb.text().startswith(("Reuniões processadas",
                                                              "Descreva seu perfil",
                                                              "Ao gerar com IA"))]
        self.assertEqual(len(wrapped), 3)   # card + subtítulo + aviso de IA presentes
        for lb in wrapped:
            with self.subTest(texto=lb.text()[:30]):
                self.assertGreaterEqual(lb.height(), lb.heightForWidth(lb.width()))
