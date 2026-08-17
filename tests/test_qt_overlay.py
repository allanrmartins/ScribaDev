"""Pílula Qt (#46): posição/clamp (puro) + smoke offscreen dos modos, callbacks e paint.

A lógica de posição e formatação não precisa de PySide6; o smoke constrói a pílula
offscreen e exige o extra `qt` (pula com skip se ausente).
"""

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
class PosAndFormatTests(unittest.TestCase):
    def test_pos_in_bounds(self):
        from scriba.qt import overlay

        self.assertTrue(overlay._pos_in_bounds(100, 100, 0, 0, 1920, 1080))
        self.assertFalse(overlay._pos_in_bounds(-5000, -5000, 0, 0, 1920, 1080))
        self.assertFalse(overlay._pos_in_bounds(3000, 100, 0, 0, 1920, 1080))
        # 2º monitor à direita: mesmo x=2000 cabe num desktop de 3840 de largura
        self.assertTrue(overlay._pos_in_bounds(2000, 100, 0, 0, 3840, 1080))

    def test_fmt_elapsed(self):
        from scriba.qt import overlay

        self.assertEqual(overlay._fmt_elapsed(0), "00:00")
        self.assertEqual(overlay._fmt_elapsed(65), "01:05")
        self.assertEqual(overlay._fmt_elapsed(3661), "1:01:01")

    def test_clamp_preserva_pos_valida_e_agarra_a_invalida(self):
        """Rotação de monitor: a posição salva é CLAMPADA à área visível, nunca
        descartada p/ o default (a pílula 'esquecia' o lugar ao girar a tela)."""
        from PySide6.QtWidgets import QApplication

        from scriba.qt import overlay

        QApplication.instance() or QApplication([])
        g = QApplication.primaryScreen().geometry()
        # posição válida (dentro da tela): sai intacta
        ok = (g.x() + 10, g.y() + 10)
        self.assertEqual(overlay._clamp_to_screens(*ok), ok)
        # posição de um monitor que 'girou' (muito além da borda): agarra ao canto
        # visível mais próximo — e NÃO no default centro-inferior
        cx, cy = overlay._clamp_to_screens(g.x() + g.width() + 5000, g.y() + 20)
        self.assertEqual(cx, g.x() + g.width() - overlay._W)
        self.assertEqual(cy, g.y() + 20)
        self.assertNotEqual((cx, cy), overlay._default_pos())


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class PillSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _pill(self):
        from scriba.qt.overlay import RecordingPill

        self.events = []
        return RecordingPill(
            on_stop=lambda: self.events.append("stop"),
            on_discard=lambda: self.events.append("discard"),
            on_record=lambda: self.events.append("record"),
            on_speakers=lambda n: self.events.append(("spk", n)),
            on_split=lambda: self.events.append("split"),
        )

    def test_modos_repintam_sem_erro(self):
        pill = self._pill()
        for setup in (
            lambda: pill.set_mode("recording"),
            lambda: pill.set_mode("idle"),
            lambda: pill.set_processing("Transcrevendo…"),
            lambda: (pill.set_mode("recording"), pill.set_status("finalizando…")),
        ):
            setup()
            pill.repaint()  # exercita cada ramo do paintEvent

    def test_restyle_theme_repinta_sem_erro(self):
        pill = self._pill()
        pill.set_mode("recording")
        pill.restyle_theme()   # troca a quente (#70): força repaint (paintEvent lê active())

    def test_callbacks_por_regiao(self):
        pill = self._pill()
        pill.set_mode("recording")
        pill._fire("stop")
        pill._fire("discard")
        pill._fire("split")
        self.assertEqual(self.events, ["stop", "discard", "split"])

    def test_record_so_no_idle(self):
        pill = self._pill()
        pill.set_mode("idle")
        # no modo idle, a única região é "rec"
        self.assertEqual(set(pill._regions()), {"rec"})
        pill._fire("rec")
        self.assertIn("record", self.events)

    def test_stepper_participantes(self):
        pill = self._pill()
        pill.reset_speakers(0)          # sugere 1, não confirmado
        self.assertEqual(pill._spk, 1)
        self.assertFalse(pill._spk_committed)
        pill._fire("spk_plus")          # 2, confirma
        self.assertEqual(pill._spk, 2)
        self.assertTrue(pill._spk_committed)
        pill._fire("spk_minus")         # 1
        for _ in range(30):
            pill._fire("spk_plus")      # clamp em 20
        self.assertEqual(pill._spk, 20)
        self.assertEqual(self.events[-1], ("spk", 20))

    def test_status_esconde_botoes(self):
        pill = self._pill()
        pill.set_mode("recording")
        self.assertTrue(pill._regions())        # tem botões
        pill.set_status("finalizando…")
        self.assertEqual(pill._regions(), {})   # texto fixo: sem botões
        pill.set_elapsed(99)                     # ignorado enquanto há status
        self.assertNotEqual(pill._elapsed, "01:39")
        pill.clear_status()
        self.assertTrue(pill._regions())

    def test_show_hide_posiciona_e_persiste(self):
        from scriba import util
        from scriba.qt import overlay

        orig = util.STATE_PATH
        util.STATE_PATH = Path(tempfile.mkdtemp(prefix="scriba_qt_pill_")) / "state.json"
        try:
            pill = self._pill()
            pill.show()
            self.assertTrue(pill.isVisible())
            # posição salva em (x,y) válidos dentro da tela offscreen
            pill.move(120, 60)
            overlay._save_pos(120, 60)
            self.assertEqual(util.read_state().get("overlay_pos"), [120, 60])
            pill.hide()
            self.assertFalse(pill.isVisible())
            pill.destroy()
        finally:
            util.STATE_PATH = orig

    def test_transicao_de_estagio_nao_teleporta_a_pilula(self):
        """Bug real: o main re-chama show() a cada estágio (gravando→transcrevendo→
        resumindo) e o re-read do state teleportava a pílula. A posição da SESSÃO
        (último drag) tem que vencer o arquivo em qualquer show() subsequente."""
        import json

        from scriba import util
        from scriba.qt import overlay

        orig = util.STATE_PATH
        util.STATE_PATH = Path(tempfile.mkdtemp(prefix="scriba_qt_pill_")) / "state.json"
        try:
            util.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            util.STATE_PATH.write_text(json.dumps({"overlay_pos": [30, 30]}), encoding="utf-8")
            pill = self._pill()
            pill.show()
            self.assertEqual((pill.x(), pill.y()), (30, 30))
            # usuário arrasta p/ (200, 90) durante a call
            pill.move(200, 90)
            pill._end_drag()
            # arquivo diverge (outra escrita qualquer) e o main re-mostra num estágio
            util.STATE_PATH.write_text(json.dumps({"overlay_pos": [30, 30]}), encoding="utf-8")
            pill.hide()
            pill.show()   # transição: NÃO pode voltar p/ (30, 30)
            self.assertEqual((pill.x(), pill.y()), (200, 90))
            pill.hide()
            pill.destroy()
        finally:
            util.STATE_PATH = orig

    def test_show_reclampa_pos_salva_fora_da_tela(self):
        """Posição salva que ficou FORA (monitor girado): o show re-agarra à borda
        visível em vez de resetar p/ o default — a intenção do usuário sobrevive."""
        import json

        from PySide6.QtWidgets import QApplication

        from scriba import util
        from scriba.qt import overlay

        orig = util.STATE_PATH
        util.STATE_PATH = Path(tempfile.mkdtemp(prefix="scriba_qt_pill_")) / "state.json"
        try:
            g = QApplication.primaryScreen().geometry()
            longe = [g.x() + g.width() + 5000, g.y() + 20]  # além da borda direita
            util.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            util.STATE_PATH.write_text(json.dumps({"overlay_pos": longe}), encoding="utf-8")
            pill = self._pill()
            pill.show()
            self.assertEqual(pill.x(), g.x() + g.width() - overlay._W)  # borda, não default
            self.assertEqual(pill.y(), g.y() + 20)                      # intenção preservada
            self.assertNotEqual((pill.x(), pill.y()), overlay._default_pos())
            pill.hide()
            pill.destroy()
        finally:
            util.STATE_PATH = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
