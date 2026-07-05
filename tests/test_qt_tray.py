"""Bandeja Qt (#52): smoke offscreen do menu, sync de estado e troca de ícone."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None


class _FakeCfgOut:
    def resolved_recordings_dir(self):
        return Path(os.environ.get("TEMP", ".")) / "scriba_fake_rec"

    def resolved_export_dir(self):
        return Path(os.environ.get("TEMP", ".")) / "scriba_fake_notes"


class _FakeApp:
    def __init__(self, recording=False, speakers=2):
        self._rec = recording
        self._spk = speakers
        self.cfg = type("C", (), {"output": _FakeCfgOut()})()

    def is_recording(self):
        return self._rec

    def status_text(self):
        return "gravando" if self._rec else "aguardando call"

    def current_speakers(self):
        return self._spk

    # métodos chamados por ações (não exercitados no _sync)
    def show_main(self): pass
    def show_notes(self): pass
    def show_action_hub(self, note_path=None): pass
    def show_log(self): pass
    def show_settings(self): pass


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class TrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_sync_esconde_acoes_fora_de_gravacao(self):
        from scriba.qt.tray import Tray

        tray = Tray(_FakeApp(recording=False))
        tray._sync()
        self.assertTrue(tray._act_record.isVisible())
        self.assertFalse(tray._act_stop.isVisible())
        self.assertFalse(tray._act_split.isVisible())
        self.assertFalse(tray._act_discard.isVisible())
        self.assertIn("aguardando", tray._status.text())

    def test_sync_mostra_acoes_e_marca_participantes(self):
        from scriba.qt.tray import Tray

        tray = Tray(_FakeApp(recording=True, speakers=3))
        tray._sync()
        self.assertFalse(tray._act_record.isVisible())
        self.assertTrue(tray._act_stop.isVisible())
        self.assertTrue(tray._act_speakers.isVisible())
        self.assertTrue(tray._spk_acts[3].isChecked())
        self.assertFalse(tray._spk_acts[1].isChecked())

    def test_menu_tem_entrada_pendencias(self):
        from scriba.qt.tray import Tray

        tray = Tray(_FakeApp())
        labels = [a.text() for a in tray._menu.actions()]
        self.assertIn("Pendências", labels)
        # posicionada junto de "Notas" (logo depois)
        self.assertEqual(labels.index("Pendências"), labels.index("Notas") + 1)

    def test_set_recording_troca_icone_e_tooltip(self):
        from scriba.qt.tray import Tray

        tray = Tray(_FakeApp())
        tray.set_recording(True, "ScribaDev — gravando 00:12", dim=True)
        self.assertEqual(tray._tray.toolTip(), "ScribaDev — gravando 00:12")
        self.assertFalse(tray._tray.icon().isNull())
        tray.start()
        tray.stop()  # não deve levantar offscreen


if __name__ == "__main__":
    unittest.main(verbosity=2)
