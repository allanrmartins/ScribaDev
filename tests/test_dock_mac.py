"""Clique no Dock abre a janela (macOS): o handler do Apple Event "reabrir"."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@unittest.skipUnless(sys.platform == "darwin", "só macOS")
class ReopenHandlerTests(unittest.TestCase):
    def test_reabrir_chama_o_callback(self):
        from Foundation import NSAppleEventManager

        from scriba import dock_mac

        chamadas = []
        self.assertTrue(dock_mac.install_reopen_handler(lambda: chamadas.append(1)))
        self.assertTrue(dock_mac.install_reopen_handler(lambda: chamadas.append(2)))  # 2x ok
        dock_mac._handler.handleReopen_withReplyEvent_(None, None)
        self.assertEqual(chamadas, [2])
        mgr = NSAppleEventManager.sharedAppleEventManager()
        mgr.removeEventHandlerForEventClass_andEventID_(
            dock_mac._fourcc("aevt"), dock_mac._fourcc("rapp"))

    def test_callback_que_falha_nao_derruba(self):
        from scriba import dock_mac

        def falha():
            raise RuntimeError("x")

        dock_mac.install_reopen_handler(falha)
        with self.assertLogs("scriba.dock_mac", "ERROR"):
            dock_mac._handler.handleReopen_withReplyEvent_(None, None)


if __name__ == "__main__":
    unittest.main()
