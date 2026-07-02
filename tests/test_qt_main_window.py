"""Capa Qt (#47): smoke offscreen do render de status, tick, barra de update e X->esconde."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None


class _FakeApp:
    def __init__(self, recording=False, call_active=False):
        self._rec = recording
        self.call_active = call_active
        self.update_news = None

    def is_recording(self): return self._rec
    def current_call_app(self): return "Teams"
    def recording_duration(self): return 72.0
    def call_duration(self): return 30.0
    def show_settings(self): pass
    def show_notes(self): pass
    def show_log(self): pass
    def start_recording(self, *a): pass
    def stop_recording(self, **k): pass
    def ui(self, fn): fn()


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _win(self, **kw):
        from scriba.qt.main_window import MainWindow

        return MainWindow(_FakeApp(**kw))

    def test_render_status_cria_uma_linha_por_item(self):
        win = self._win()
        win._render_status([
            ("ok", "Detecção Teams", "ativa"),
            ("warn", "Áudio", "indisponível"),
            ("off", "Resumo", "desativado"),
        ])
        self.assertEqual(win._rows_lay.count(), 3)
        win._render_status([("ok", "Só um", "detalhe")])  # re-render limpa os anteriores
        self.assertEqual(win._rows_lay.count(), 1)

    def test_tick_idle(self):
        win = self._win(recording=False, call_active=False)
        win.setVisible(True)
        win._tick()
        self.assertIn("Nenhuma", win._call_state.text())
        self.assertEqual(win._call_timer.text(), "—")

    def test_tick_gravando(self):
        win = self._win(recording=True)
        win.setVisible(True)
        win._tick()
        self.assertIn("Gravando", win._call_state.text())
        self.assertEqual(win._call_timer.text(), "01:12")
        self.assertEqual(win._rec_btn.text(), "■  Parar e processar")

    def test_tick_em_call_sem_gravar(self):
        win = self._win(recording=False, call_active=True)
        win.setVisible(True)
        win._tick()
        self.assertIn("Em call", win._call_state.text())
        self.assertEqual(win._call_timer.text(), "00:30")

    def test_show_update_mostra_barra(self):
        win = self._win()
        self.assertTrue(win._update_bar.isHidden())
        win.show_update("0.7.0")
        self.assertFalse(win._update_bar.isHidden())
        self.assertIn("0.7.0", win._update_lbl.text())

    def test_x_esconde_nao_fecha(self):
        win = self._win()
        win.setVisible(True)
        self.assertTrue(win.isVisible())
        win.close()  # dispara closeEvent -> ignore + hide
        self.assertFalse(win.isVisible())


if __name__ == "__main__":
    unittest.main(verbosity=2)
