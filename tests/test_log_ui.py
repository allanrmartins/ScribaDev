"""Testes de scriba.log_ui.show_crash_dialog: só os guards que NÃO precisam de um
root Tk com display (o diálogo em si é coberto por smoke manual, como settings_ui)."""

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import log_ui  # noqa: E402


class CrashDialogGuardTests(unittest.TestCase):
    def tearDown(self):
        log_ui._crash_open = False  # não vazar estado entre testes

    def test_root_none_nao_abre(self):
        # crash cedo demais (UI ainda não existe) -> retorna sem tocar em Tk
        log_ui._crash_open = False
        log_ui.show_crash_dialog(types.SimpleNamespace(root=None), "traceback")
        self.assertFalse(log_ui._crash_open)

    def test_nao_empilha_quando_ja_aberto(self):
        # já há um diálogo aberto -> retorna antes de criar outro Toplevel
        log_ui._crash_open = True
        log_ui.show_crash_dialog(types.SimpleNamespace(root=object()), "traceback")
        self.assertTrue(log_ui._crash_open)  # inalterado; nenhuma exceção


if __name__ == "__main__":
    unittest.main(verbosity=2)
