"""#101: notificações por SO — toasts no Windows, osascript no macOS (#104 M6),
no-op logado nos demais."""

import sys
import unittest
from pathlib import Path

from scriba import notify


class TestSelecaoPorSO(unittest.TestCase):
    def test_notifier_do_so_atual(self):
        if sys.platform == "win32":
            esperado = notify._WindowsNotifier
        elif sys.platform == "darwin":
            esperado = notify._MacNotifier
        else:
            esperado = notify._NoopNotifier
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


class ToastThreadTests(unittest.TestCase):
    """#161: o toast fala com o serviço de notificações por COM (cross-process) —
    a entrega roda SEMPRE em thread própria; quem chamou nunca fica preso."""

    def _app(self, notifier):
        from scriba.main import ScribaApp

        app = ScribaApp.__new__(ScribaApp)
        app.notifier = notifier
        return app

    def test_toast_roda_em_thread_propria(self):
        import threading

        seen = {}
        done = threading.Event()

        class _N:
            def info(self, title, body):
                seen["thread"] = threading.current_thread().name
                seen["args"] = (title, body)
                done.set()

        self._app(_N())._toast("Gravando reunião", "corpo")
        self.assertTrue(done.wait(5))
        self.assertEqual(seen["args"], ("Gravando reunião", "corpo"))
        self.assertNotEqual(seen["thread"], threading.current_thread().name)

    def test_erro_do_notifier_nao_propaga(self):
        import threading

        tried = threading.Event()

        class _Boom:
            def info(self, title, body):
                tried.set()
                raise RuntimeError("serviço fora")

        self._app(_Boom())._toast("x")          # não pode estourar p/ quem chamou
        self.assertTrue(tried.wait(5))
        for t in threading.enumerate():          # a thread do toast morre limpa
            if t.name == "toast":
                t.join(5)

    def test_sem_notifier_e_no_op(self):
        self._app(None)._toast("x", "y")


if __name__ == "__main__":
    unittest.main()
