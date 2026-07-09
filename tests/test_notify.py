"""#101: notificações por SO — toasts no Windows, no-op logado no POSIX."""

import sys
import unittest
from pathlib import Path

from scriba import notify


class TestSelecaoPorSO(unittest.TestCase):
    def test_notifier_do_so_atual(self):
        esperado = notify._WindowsNotifier if sys.platform == "win32" else notify._NoopNotifier
        self.assertIs(notify.Notifier, esperado)


class TestNoopNotifier(unittest.TestCase):
    """O no-op roda em qualquer SO (não toca windows_toasts) e nunca levanta."""

    def setUp(self):
        self.n = notify._NoopNotifier()

    def test_info_nao_levanta(self):
        with self.assertLogs("scriba.notify", level="INFO"):
            self.n.info("Título", "corpo")

    def test_notes_ready_nao_levanta(self):
        with self.assertLogs("scriba.notify", level="INFO"):
            self.n.notes_ready(Path("nota.md"))

    def test_test_nao_levanta(self):
        with self.assertLogs("scriba.notify", level="INFO"):
            self.n.test()


if __name__ == "__main__":
    unittest.main()
