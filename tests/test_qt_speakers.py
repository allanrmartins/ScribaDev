"""Participantes Qt (#51): has_labelable_voices + AskNumSpeakers + LabelSpeakersDialog."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class SpeakersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_has_labelable_voices(self):
        from scriba.qt import speakers_ui

        d = Path(tempfile.mkdtemp(prefix="scriba_hv_"))
        self.assertFalse(speakers_ui.has_labelable_voices(d))
        (d / "voices.json").write_text('{"Ana": {}}', encoding="utf-8")
        self.assertTrue(speakers_ui.has_labelable_voices(d))

    def test_ask_confirma_inteiro(self):
        from scriba.qt.speakers_ui import AskNumSpeakers

        got = []
        w = AskNumSpeakers(got.append, last_value=None, timeout_seconds=0)
        w._entry.setText("3")
        w._confirm()
        self.assertEqual(got, [3])

    def test_ask_nao_sei_e_zero(self):
        from scriba.qt.speakers_ui import AskNumSpeakers

        got = []
        w = AskNumSpeakers(got.append, timeout_seconds=0)
        w._entry.setText("0")
        w._confirm()                          # 0 não vale: avisa, não resolve
        self.assertEqual(got, [])
        self.assertIn("0 não vale", w._hint.text())
        w._finish(None)                       # "Não sei / automático"
        self.assertEqual(got, [None])

    def test_ask_timeout_cai_no_automatico(self):
        from scriba.qt.speakers_ui import AskNumSpeakers

        got = []
        w = AskNumSpeakers(got.append, timeout_seconds=5)
        w._left = 0
        w._tick()
        self.assertEqual(got, [None])

    def test_ask_uma_chamada_so(self):
        from scriba.qt.speakers_ui import AskNumSpeakers

        got = []
        w = AskNumSpeakers(got.append, timeout_seconds=0)
        w._entry.setText("2")
        w._confirm()
        w._finish(None)                       # 2ª chamada é ignorada
        w.close()
        self.assertEqual(got, [2])

    def _folder_with_voices(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_lbl_"))
        (d / "voices.json").write_text(json.dumps({
            "Ana": {"auto": True}, "Participante 2": {}, "Participante 3": {},
        }), encoding="utf-8")
        return d

    def test_dialog_monta_uma_linha_por_voz(self):
        from scriba.qt.speakers_ui import LabelSpeakersDialog

        dlg = LabelSpeakersDialog(self._folder_with_voices())
        self.assertEqual(len(dlg._rows), 3)

    def test_dialog_sem_vozes(self):
        from scriba.qt.speakers_ui import LabelSpeakersDialog

        dlg = LabelSpeakersDialog(Path(tempfile.mkdtemp(prefix="scriba_nov_")))
        self.assertEqual(dlg._rows, [])

    def test_dialog_marcado_sem_nome_avisa(self):
        from scriba.qt.speakers_ui import LabelSpeakersDialog

        dlg = LabelSpeakersDialog(self._folder_with_voices())
        label, entry, chk, _w = dlg._rows[1]   # "Participante 2", vazio
        entry.setText("")
        chk.setChecked(True)
        dlg._save()
        self.assertIn("Dê um nome", dlg._status.text())

    def test_dialog_salva_renomeia(self):
        from scriba.qt.speakers_ui import LabelSpeakersDialog

        saved = []
        dlg = LabelSpeakersDialog(self._folder_with_voices(), on_saved=lambda: saved.append(1))
        _l, entry, chk, _w = dlg._rows[1]
        entry.setText("Bruno")
        chk.setChecked(True)
        dlg._save()                            # relabel_speakers é tolerante a falha
        self.assertIn("Aprendi", dlg._status.text())

    def test_dialog_remove_voz_some_da_linha_e_chama_remove(self):
        from unittest import mock

        from scriba.qt.speakers_ui import LabelSpeakersDialog

        dlg = LabelSpeakersDialog(self._folder_with_voices())
        label, _entry, _chk, row_w = dlg._rows[2]   # "Participante 3"
        dlg._remove_row(label, row_w)
        self.assertIn(label, dlg._removed)
        self.assertFalse(row_w.isVisible())
        self.assertIn("removida", dlg._status.text())
        with mock.patch("scriba.notes.remove_speakers") as rm:
            dlg._save()
        rm.assert_called_once()
        self.assertEqual(rm.call_args[0][1], {"Participante 3"})
        self.assertIn("Removi", dlg._status.text())

    def test_dialog_voz_removida_nao_exige_nome_no_save(self):
        # voz marcada mas removida não deve travar o save por "sem nome"
        from unittest import mock

        from scriba.qt.speakers_ui import LabelSpeakersDialog

        dlg = LabelSpeakersDialog(self._folder_with_voices())
        label, entry, chk, row_w = dlg._rows[1]     # "Participante 2"
        chk.setChecked(True); entry.setText("")
        dlg._remove_row(label, row_w)
        with mock.patch("scriba.notes.remove_speakers"), \
             mock.patch("scriba.notes.relabel_speakers"):
            dlg._save()
        self.assertNotIn("Dê um nome", dlg._status.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
