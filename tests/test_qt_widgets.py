"""Fundação de widgets Qt (#61): clamp de geometria ao monitor + persistência do splitter.

Exige PySide6 (pula sem o extra 'qt'). O clamp evita a janela abrir fora da tela quando o
layout de monitores muda; a persistência do splitter guarda a divisão dos painéis no
state.json (mesma infra do remember_geometry, não QSettings)."""

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
class ClampTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_geometria_fora_da_tela_volta_pra_area_util(self):
        from PySide6.QtGui import QGuiApplication

        from scriba.qt.widgets import _clamp_to_screen

        a = QGuiApplication.primaryScreen().availableGeometry()
        x, y, w, h = _clamp_to_screen([-9000, -9000, 800, 600])
        self.assertGreaterEqual(x, a.left())
        self.assertGreaterEqual(y, a.top())
        self.assertLessEqual(x + w - 1, a.right())
        self.assertLessEqual(y + h - 1, a.bottom())

    def test_geometria_valida_nao_muda(self):
        from PySide6.QtGui import QGuiApplication

        from scriba.qt.widgets import _clamp_to_screen

        a = QGuiApplication.primaryScreen().availableGeometry()
        r = [a.left() + 10, a.top() + 10, 400, 300]
        self.assertEqual(_clamp_to_screen(list(r)), r)


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class SplitterPersistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from scriba import util

        self._orig = util.STATE_PATH
        util.STATE_PATH = Path(tempfile.mkdtemp(prefix="scriba_split_")) / "state.json"

    def tearDown(self):
        from scriba import util

        util.STATE_PATH = self._orig

    def _split(self):
        from PySide6.QtWidgets import QLabel, QSplitter

        s = QSplitter()
        s.addWidget(QLabel("a"))
        s.addWidget(QLabel("b"))
        s.resize(1000, 400)
        return s

    def test_salva_e_restaura_a_divisao(self):
        from scriba.qt import widgets

        s1 = self._split()
        widgets.remember_splitter(s1, "k")
        s1.setSizes([250, 750])
        s1._splitter_debounce.timeout.emit()   # dispara o save (pula o debounce)

        # um novo splitter com a mesma chave restaura a divisão salva
        s2 = self._split()
        widgets.remember_splitter(s2, "k")      # restore acontece na chamada (state salvo)
        self.assertEqual(sum(s2.sizes()), sum(s1.sizes()))
        self.assertAlmostEqual(s2.sizes()[0], 250, delta=6)

    def test_sem_estado_aplica_default_proporcional_com_clamp(self):
        from scriba.qt import widgets

        s = self._split()   # largura 1000 -> 28% = 280, dentro do clamp [240..360]
        widgets.remember_splitter(s, "novo")
        self.app.processEvents()                # dispara o _apply_default (singleShot 0)
        self.assertGreaterEqual(s.sizes()[0], 240)
        self.assertLessEqual(s.sizes()[0], 360)


if __name__ == "__main__":
    unittest.main(verbosity=2)
