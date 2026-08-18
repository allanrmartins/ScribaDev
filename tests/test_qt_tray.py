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
    def __init__(self, recording=False, speakers=2, timesheet_enabled=False):
        self._rec = recording
        self._spk = speakers
        ts = type("T", (), {"enabled": timesheet_enabled})()
        self.cfg = type("C", (), {"output": _FakeCfgOut(), "timesheet": ts})()

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
    def show_timesheet(self): pass
    def show_log(self): pass
    def show_settings(self): pass


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class TrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_set_recording_pula_com_shell_pendurado(self):
        """#161: Shell_NotifyIcon é SendMessage síncrono p/ o Explorer — com o shell
        sem responder, o pulso NÃO pode chamar setIcon/setToolTip (congelava a GUI e
        derrubou uma gravação real); com o shell de volta, atualiza de novo."""
        from unittest import mock

        from scriba.qt.tray import Tray

        tray = Tray(_FakeApp(recording=True))
        with mock.patch.object(tray._tray, "setIcon") as si, \
                mock.patch.object(tray._tray, "setToolTip") as st, \
                mock.patch("scriba.shellprobe.responsive", return_value=False):
            tray.set_recording(True, "gravando 0:07", dim=True)
            tray.set_recording(True, "gravando 0:08", dim=False)
        si.assert_not_called()
        st.assert_not_called()
        with mock.patch.object(tray._tray, "setIcon") as si2, \
                mock.patch.object(tray._tray, "setToolTip") as st2, \
                mock.patch("scriba.shellprobe.responsive", return_value=True):
            tray.set_recording(True, "gravando 0:09")
        si2.assert_called_once()
        st2.assert_called_once()

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

    def test_menu_timesheet_segue_a_dormencia(self):
        """#118/#123: item 'Apontamento de horas' junto de Pendências, visível só
        com [timesheet].enabled — o _sync relê a config a cada abertura do menu."""
        from scriba.qt.tray import Tray

        tray = Tray(_FakeApp(timesheet_enabled=False))
        labels = [a.text() for a in tray._menu.actions()]
        self.assertIn("Apontamento de horas", labels)
        self.assertEqual(labels.index("Apontamento de horas"),
                         labels.index("Pendências") + 1)
        tray._sync()
        self.assertFalse(tray._act_timesheet.isVisible())
        # ativação em runtime (settings): próximo _sync já mostra, sem reiniciar
        tray.app.cfg.timesheet.enabled = True
        tray._sync()
        self.assertTrue(tray._act_timesheet.isVisible())

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
