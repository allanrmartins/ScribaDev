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
