"""Fundação Qt (#45): smoke offscreen dos widgets-base e do painel do spike.

Roda na suíte normal (`unittest discover`). Os testes constroem widgets Qt, então
exigem PySide6 e um QApplication offscreen (QT_QPA_PLATFORM=offscreen, setado antes
de qualquer import de QtWidgets) — pulam com skip se PySide6 não estiver instalado,
para não travar quem roda a suíte sem o extra `qt`. O CI instala `.[qt]`, então lá
eles rodam. O sistema de temas (completude/contraste) é testado em test_qt_theme.
"""

import importlib.util
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # sem display no CI

_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class QtWidgetSmokeTests(unittest.TestCase):
    """Constrói cada widget-base offscreen: pega erro de runtime (paintEvent, Property,
    layout) que py_compile e a suíte pura não veem."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_theme_apply_em_todos_os_temas(self):
        from scriba.qt import theme

        for th in theme.themes():
            theme.apply(self.app, th)
            self.assertTrue(self.app.styleSheet())
        theme.apply(self.app)  # volta ao ativo

    def test_modern_button_primary_e_secondary(self):
        from scriba.qt.widgets import ModernButton, flash_button

        clicks = []
        b = ModernButton("Salvar", lambda: clicks.append(1), kind="primary")
        self.assertEqual(b.property("kind"), "primary")
        b.click()
        self.assertEqual(clicks, [1])
        b.set_text("Salvo!")
        self.assertEqual(b.text(), "Salvo!")
        flash_button(b, "feito", "Salvar")  # não deve levantar

    def test_toggle_switch(self):
        from scriba.qt.widgets import ToggleSwitch

        seen = []
        t = ToggleSwitch(checked=False)
        t.toggled.connect(seen.append)
        t.setChecked(True)
        self.assertTrue(t.isChecked())
        self.assertEqual(seen, [True])
        t.repaint()  # exercita o paintEvent (interpolação de cor + knob)

    def test_animated_checkbox(self):
        from scriba.qt.widgets import AnimatedCheckBox

        c = AnimatedCheckBox("Ativar")
        self.assertFalse(c.isChecked())
        self.assertEqual(c.get_fill(), 0.0)     # desmarcado = indicador vazio
        c.repaint()
        c.setChecked(True)
        self.assertTrue(c.isChecked())
        c._anim.stop(); c.set_fill(1.0)          # fim da animação (tick verde cheio)
        self.assertEqual(c.get_fill(), 1.0)
        c.repaint()                              # exercita o paintEvent (tick + texto)
        c.setChecked(False); c._anim.stop(); c.set_fill(0.0)
        c.repaint()

    def test_animated_checkbox_nao_come_o_texto(self):
        """Regressão (texto comido no Log): o texto é pintado à mão a partir de
        _BOX+_GAP, então o sizeHint precisa cobrir ESSA geometria (o herdado usava o
        indicador do estilo, mais estreito) e o layout não pode encolher abaixo dela."""
        from scriba.qt.widgets import AnimatedCheckBox

        for texto in ("auto-atualizar", "Dia", "Hora ≥"):
            with self.subTest(texto=texto):
                c = AnimatedCheckBox(texto)
                need = c._BOX + c._GAP + c.fontMetrics().horizontalAdvance(texto)
                self.assertGreaterEqual(c.sizeHint().width(), need)
                self.assertEqual(c.minimumSizeHint(), c.sizeHint())

    def test_stepper(self):
        from scriba.qt.widgets import Stepper

        seen = []
        s = Stepper(value=10, step=5, lo=0, hi=30, suffix=" s")
        s.valueChanged.connect(seen.append)
        s._bump(+1)
        self.assertEqual(s.value(), 15)
        s.setValue(999)  # clamp no hi
        self.assertEqual(s.value(), 30)
        s.setValue(-999)  # clamp no lo
        self.assertEqual(s.value(), 0)
        self.assertEqual(seen, [15, 30, 0])

    def test_make_entry_placeholder(self):
        from scriba.qt.widgets import make_entry

        e = make_entry("Buscar…")
        self.assertEqual(e.placeholderText(), "Buscar…")
        self.assertEqual(e.text(), "")  # placeholder não polui o valor

    def test_date_filter_liga_desliga(self):
        from PySide6.QtCore import QDate

        from scriba.qt.widgets import DateFilter

        seen = []
        f = DateFilter("de")
        f.changed.connect(lambda: seen.append(1))
        self.assertEqual(f.br(), "")             # desmarcado (padrão) = sem filtro
        self.assertFalse(f._edit.isEnabled())    # editor desabilitado enquanto desligado
        f._chk.setChecked(True)
        f._edit.setDate(QDate(2026, 7, 2))
        self.assertEqual(f.br(), "02/07/2026")   # ligado = data em DD/MM/AAAA (formato BR)
        self.assertTrue(seen)                    # emitiu changed
        f.clear()
        self.assertEqual(f.br(), "")             # clear desliga o filtro

    def test_time_filter_liga_desliga(self):
        from PySide6.QtCore import QTime

        from scriba.qt.widgets import TimeFilter

        f = TimeFilter("Hora ≥")
        self.assertEqual(f.hhmm(), "")           # desligado = sem filtro
        f._chk.setChecked(True)
        f._edit.setTime(QTime(9, 30))
        self.assertEqual(f.hhmm(), "09:30")
        f.clear()
        self.assertEqual(f.hhmm(), "")

    def test_troca_de_tema_repinta_widget(self):
        from scriba.qt import theme
        from scriba.qt.widgets import ToggleSwitch

        orig = theme._active
        try:
            t = ToggleSwitch(checked=True)
            for th in theme.themes():
                theme._active = th  # sem tocar o state real; só o cache em memória
                t.repaint()  # paintEvent lê theme.active() → cor do tema atual
        finally:
            theme._active = orig

    def test_spike_panel_constroi_e_reestiliza(self):
        from scriba.qt import spike, theme

        orig = theme._active
        try:
            panel = spike.SpikePanel()
            panel.show()
            for th in theme.themes():
                theme._active = th
                panel.refresh()  # troca fonte mono do bloco de código + barra de título
        finally:
            theme._active = orig
            panel.close()

    def test_remember_geometry_restaura_salvo(self):
        import tempfile
        from pathlib import Path

        from PySide6.QtWidgets import QWidget

        from scriba import util
        from scriba.qt import widgets

        orig = util.STATE_PATH
        util.STATE_PATH = Path(tempfile.mkdtemp(prefix="scriba_qt_st_")) / "state.json"
        try:
            util.update_state(_geom_qt={"win_x": [120, 60, 500, 400]})
            w = QWidget()
            widgets.remember_geometry(w, "win_x")
            g = w.geometry()
            self.assertEqual((g.x(), g.y(), g.width(), g.height()), (120, 60, 500, 400))
        finally:
            util.STATE_PATH = orig

    def test_is_geom(self):
        from scriba.qt.widgets import _is_geom

        self.assertTrue(_is_geom([1, 2, 3, 4]))
        self.assertFalse(_is_geom([1, 2, 3]))
        self.assertFalse(_is_geom("100x100+0+0"))  # formato do tkinter — não confundir
        self.assertFalse(_is_geom(None))

    def test_pump_drena_da_fila(self):
        from scriba.qt.pump import UiPump

        got = []
        pump = UiPump()
        pump.ui(lambda: got.append("a"))
        pump.ui(lambda: (_ for _ in ()).throw(RuntimeError("boom")))  # não pode derrubar
        pump.ui(lambda: got.append("b"))
        pump.drain()
        self.assertEqual(got, ["a", "b"])  # o do meio levantou e foi ignorado


if __name__ == "__main__":
    unittest.main(verbosity=2)
