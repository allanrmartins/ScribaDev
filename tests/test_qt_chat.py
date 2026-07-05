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

    def test_restyle_theme_reestila_bolhas_sem_quebrar(self):
        from scriba.qt import theme

        win = self._win()
        win._append_user("oi")
        win._append_assistant("**resposta**")
        win._append_system("aviso")
        orig = theme._active
        self.addCleanup(lambda: setattr(theme, "_active", orig))
        theme._active = theme.by_slug("light")
        win.restyle_theme()                                   # troca a quente (#70): não estoura
        user = [b for b in win._bubbles if b.property("bubbleKind") == "user"]
        self.assertTrue(user)
        r, g, b = (int(theme.by_slug("light").accent.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        self.assertIn(f"{r}, {g}, {b}", user[0].styleSheet())          # bolha user pegou o acento novo

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

    def test_summary_payload_nunca_despeja_transcricao(self):
        win = self._win(transcript="FALA SECRETA que nao deve vazar no prompt")
        p = win._summary_payload("o que decidiu?")
        self.assertIn("## Resumo", p)                        # o resumo entra
        self.assertNotIn("FALA SECRETA", p)                  # a transcrição NUNCA
        self.assertTrue(p.rstrip().endswith("Pergunta: o que decidiu?\nResposta:"))

    def test_resposta_vazia_nao_conta_nem_avisa(self):
        win = self._win()
        for _ in range(8):
            win._answered("q", None)                          # falha da IA: não entra no histórico
        self.assertEqual(win._history, [])
        self.assertFalse(win._warned)

    def test_aviso_rearmado_apos_clear(self):
        win = self._win()
        for i in range(6):
            win._answered(f"q{i}", f"a{i}")
        self.assertTrue(win._warned)
        win._clear_context()
        self.assertFalse(win._warned)                        # /clear rearma o aviso
        for i in range(6):
            win._answered(f"r{i}", f"b{i}")
        self.assertTrue(win._warned)                         # avisou de novo no 2º ciclo

    def test_autoscroll_acompanha_o_fim_e_respeita_subida(self):
        win = self._win()
        win.resize(400, 240); win.show()
        self.app.processEvents(); self.app.processEvents()
        for _ in range(6):
            win._append_assistant("resposta longa " + "abc " * 30)
        self.app.processEvents(); self.app.processEvents()
        bar = win._scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)                     # há conteúdo transbordando
        self.assertGreaterEqual(bar.value(), bar.maximum() - 8)  # rolou sozinho p/ o fim
        bar.setValue(0); self.app.processEvents()
        self.assertFalse(win._autoscroll)                        # usuário subiu -> pausa
        bar.setValue(bar.maximum()); self.app.processEvents()
        self.assertTrue(win._autoscroll)                         # voltou ao fim -> religa
        win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
