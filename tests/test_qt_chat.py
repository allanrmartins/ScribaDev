"""Chat Qt (#48): smoke offscreen das bolhas, markdown nativo, toggle, histórico e /clear.

Não invoca o provedor de IA (_worker): simula respostas chamando _answered direto.
"""

import importlib.util
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None

_SUMMARY = "## Resumo\n\n- item A\n- item B"


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class ChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _rows(self, win):
        return win._conv_lay.count() - 1  # desconta o stretch final

    def _win(self, transcript="fala 1\nfala 2"):
        from scriba.qt.chat_ui import ChatWindow

        return ChatWindow(_SUMMARY, transcript, "Reunião de teste")

    def test_intro_e_toggle_com_transcricao(self):
        win = self._win()
        self.assertEqual(self._rows(win), 1)          # só o intro
        self.assertIsNotNone(win._toggle)             # toggle aparece (tem transcrição)

    def test_sem_transcricao_nao_tem_toggle(self):
        win = self._win(transcript=None)
        self.assertIsNone(win._toggle)

    def test_bubble_markdown_nativo(self):
        from PySide6.QtCore import Qt

        win = self._win()
        lbl = win._bubble("**negrito** e `código`", markdown=True)
        self.assertEqual(lbl.textFormat(), Qt.MarkdownText)  # substitui a mdview.py

    def test_append_incrementa_linhas(self):
        win = self._win()
        base = self._rows(win)
        win._append_user("oi")
        win._append_assistant("**resposta**")
        win._append_system("aviso")
        self.assertEqual(self._rows(win), base + 3)

    def test_answered_grava_historico(self):
        win = self._win()
        win._answered("qual o item A?", "É o **item A**.")
        self.assertEqual(len(win._history), 1)
        self.assertEqual(win._history[0][0], "qual o item A?")

    def test_aviso_apos_6_trocas(self):
        win = self._win()
        for i in range(_win_turns := 6):
            win._answered(f"p{i}", f"r{i}")
        self.assertTrue(win._warned)                  # avisou ao cruzar o limite
        self.assertEqual(len(win._history), 6)

    def test_clear_context_zera(self):
        win = self._win()
        win._answered("p", "r")
        win._append_user("mais uma")
        win._clear_context()
        self.assertEqual(len(win._history), 0)
        self.assertEqual(self._rows(win), 1)          # sobra só a confirmação do /clear


if __name__ == "__main__":
    unittest.main(verbosity=2)
