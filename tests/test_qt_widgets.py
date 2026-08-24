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


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class FocoNoMacTests(unittest.TestCase):
    """No QPA cocoa, `QCocoaWindow::raise()` termina em
    `[NSApp activateIgnoringOtherApps:YES]` — traz o processo INTEIRO para a frente.
    Como a pílula re-assere o topo a cada pulso (600 ms) e o `QWidget::show()` de
    janelas Qt.Tool/Qt.ToolTip dispara um raise implícito, gravar uma reunião roubava
    o foco do navegador o tempo todo. Aqui: a env que desliga o efeito colateral e os
    dois helpers que dividem "subir" de "ativar"."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_env_desliga_a_ativacao_implicita_do_qt_no_mac(self):
        """`import scriba.qt` precisa setar a env ANTES de qualquer QApplication —
        o Qt lê o valor uma única vez, num `static` dentro do raise()."""
        import subprocess

        code = (
            "import sys, os;"
            "sys.platform = 'darwin';"
            "os.environ.pop('QT_MAC_SET_RAISE_PROCESS', None);"
            "import scriba.qt;"
            "print(os.environ.get('QT_MAC_SET_RAISE_PROCESS'))"
        )
        raiz = str(Path(__file__).resolve().parent.parent)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=raiz)
        self.assertEqual(out.stdout.strip(), "0", out.stderr)

    def test_raise_sem_ativacao_no_darwin_nunca_cai_no_raise_do_qt(self):
        """Se a ponte Cocoa falhar (aqui: QPA offscreen), o helper devolve False e NÃO
        chama `raise_()`. Ficar um degrau abaixo no empilhamento é cosmético — a pílula
        já é WindowStaysOnTopHint; roubar o foco não é."""
        from scriba.qt import widgets

        chamou = []

        class FakeWidget:
            def raise_(self):
                chamou.append(True)

            def winId(self):
                return 0

        plat = sys.platform
        try:
            sys.platform = "darwin"
            self.assertFalse(widgets.raise_without_activation(FakeWidget()))
        finally:
            sys.platform = plat
        self.assertEqual(chamou, [])

    def test_raise_sem_ativacao_fora_do_darwin_usa_o_raise_normal(self):
        """No Windows/Linux o `raise_()` não ativa o processo — segue valendo."""
        from scriba.qt import widgets

        chamou = []

        class FakeWidget:
            def raise_(self):
                chamou.append(True)

        plat = sys.platform
        try:
            sys.platform = "win32"
            self.assertFalse(widgets.raise_without_activation(FakeWidget()))
        finally:
            sys.platform = plat
        self.assertEqual(chamou, [True])

    def test_bring_to_front_continua_levantando_e_focando(self):
        """A contrapartida: janela aberta pela bandeja AINDA vem para a frente com
        foco (no mac, com ativação explícita do processo)."""
        from scriba.qt import widgets

        chamou = []

        class FakeWidget:
            def raise_(self):
                chamou.append("raise")

            def activateWindow(self):
                chamou.append("activate")

            def winId(self):
                return 0

        widgets.bring_to_front(FakeWidget())
        self.assertEqual(chamou, ["raise", "activate"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
