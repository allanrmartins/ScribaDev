"""Teste de ScribaApp.relaunch — auto-restart do update (#19).

Regressão do bug em que o app "não reiniciava sozinho": `Path` não estava no escopo
do método e o `Path(sys.executable)` (fora do try) estourava NameError — o
relançamento nunca era agendado e o quit nunca pedido.

Nada real roda aqui: subprocess, QMessageBox, os._exit e threading.Timer são stubados,
então o teste não abre janela (o QMessageBox é modal), não relança processo nem encerra o runner.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.main import ScribaApp  # noqa: E402


@unittest.skipUnless(sys.platform == "win32", "asserta o relançador PowerShell (plat._win)")
class RelaunchTests(unittest.TestCase):
    def _app(self):
        # __new__ sem __init__: não cria Tk root nem threads; só queremos o método
        app = ScribaApp.__new__(ScribaApp)
        app.request_quit = mock.Mock()
        return app

    def test_agenda_relancamento_avisa_e_forca_saida(self):
        app = self._app()
        with mock.patch("subprocess.Popen") as popen, \
                mock.patch("builtins.open", mock.mock_open()), \
                mock.patch("scriba.main.util.ensure_app_dirs"), \
                mock.patch("PySide6.QtWidgets.QMessageBox.information") as info, \
                mock.patch("PySide6.QtWidgets.QMessageBox.warning") as warn, \
                mock.patch("os._exit") as oexit, \
                mock.patch("threading.Timer") as timer:
            app.relaunch()

            # avisou o usuário (e não caiu no aviso de falha)
            self.assertTrue(info.called)
            self.assertFalse(warn.called)
            # agendou o relançamento: powershell espera este PID sair e sobe nova instância
            self.assertTrue(popen.called)
            argv = popen.call_args[0][0]
            self.assertEqual(argv[0], "powershell")
            cmd = argv[-1]
            self.assertIn("Wait-Process", cmd)
            self.assertIn("Start-Process", cmd)
            self.assertIn("scriba.cli", cmd)
            # #74: marca a nova instância p/ dar retry no single-instance e loga a saída
            self.assertIn("SCRIBA_RELAUNCHED", cmd)
            self.assertIn("stdout", popen.call_args.kwargs)  # PS redirecionado p/ log
            # pediu o quit gracioso
            app.request_quit.assert_called_once()
            # armou o watchdog de saída forçada: 3 s, daemon, chamando os._exit(0)
            self.assertTrue(timer.called)
            self.assertEqual(timer.call_args[0][0], 3.0)
            self.assertIs(timer.return_value.daemon, True)
            timer.return_value.start.assert_called_once()
            timer.call_args[0][1]()  # executa o callback (os._exit está stubado aqui)
            oexit.assert_called_once_with(0)

    def test_falha_ao_agendar_avisa_sem_matar(self):
        app = self._app()
        with mock.patch("subprocess.Popen", side_effect=OSError("boom")), \
                mock.patch("builtins.open", mock.mock_open()), \
                mock.patch("scriba.main.util.ensure_app_dirs"), \
                mock.patch("PySide6.QtWidgets.QMessageBox.information"), \
                mock.patch("PySide6.QtWidgets.QMessageBox.warning") as warn, \
                mock.patch("os._exit") as oexit, \
                mock.patch("threading.Timer") as timer, \
                mock.patch("scriba.main.log"):
            app.relaunch()

            # não conseguiu agendar -> avisa e NÃO encerra (não deixa o usuário sem app)
            self.assertTrue(warn.called)
            app.request_quit.assert_not_called()
            self.assertFalse(timer.called)
            oexit.assert_not_called()


@unittest.skipUnless(sys.platform == "win32", "mutex nomeado é específico do Windows")
class SingleInstanceRetryTests(unittest.TestCase):
    """single_instance (plat._win) com retry: conserta o reinício pós-update
    intermitente (#74) — a nova instância espera a antiga liberar o mutex em vez
    de abortar de cara. Código movido de main.py para a camada na #100."""

    def setUp(self):
        import ctypes
        import os
        from scriba.plat import _win
        self.win = _win
        self.k32 = ctypes.windll.kernel32
        # nome ÚNICO por processo: não colide com o mutex do app real que pode estar vivo
        self.name = f"Local\\ScribaDevTest_{os.getpid()}"
        _win._mutex_handle = None

    def tearDown(self):
        if self.win._mutex_handle:
            self.k32.CloseHandle(self.win._mutex_handle)
            self.win._mutex_handle = None

    def test_sem_retry_falha_de_cara_com_mutex_preso(self):
        held = self.k32.CreateMutexW(None, False, self.name)  # simula instância anterior
        try:
            self.assertFalse(self.win.single_instance(retries=0, name=self.name))
        finally:
            self.k32.CloseHandle(held)

    def test_retry_espera_mas_falha_se_nunca_libera(self):
        held = self.k32.CreateMutexW(None, False, self.name)
        try:
            self.assertFalse(self.win.single_instance(retries=3, delay=0.01, name=self.name))
        finally:
            self.k32.CloseHandle(held)

    def test_adquire_quando_livre(self):
        self.assertTrue(self.win.single_instance(retries=2, delay=0.01, name=self.name))
        self.assertIsNotNone(self.win._mutex_handle)  # segura o handle no sucesso

    def test_retry_adquire_apos_liberacao(self):
        # a instância anterior segura o mutex e o solta durante os retries → adquire
        import threading
        held = self.k32.CreateMutexW(None, False, self.name)
        threading.Timer(0.05, lambda: self.k32.CloseHandle(held)).start()
        self.assertTrue(self.win.single_instance(retries=30, delay=0.02, name=self.name))


class PosixSpawnRelaunchTests(unittest.TestCase):
    """spawn_relaunch POSIX (mockado — roda em qualquer SO): Popen detached com
    SCRIBA_RELAUNCHED=1; a espera pela instância antiga é o retry no flock."""

    def test_popen_detached_com_env_de_relaunch(self):
        from scriba.plat import _posix

        with mock.patch("subprocess.Popen") as popen, \
                mock.patch("builtins.open", mock.mock_open()):
            _posix.spawn_relaunch(1234, Path("relaunch.log"))

        argv = popen.call_args[0][0]
        self.assertEqual(argv, [sys.executable, "-m", "scriba.cli", "run"])
        kwargs = popen.call_args.kwargs
        self.assertTrue(kwargs["start_new_session"])  # sobrevive à morte do pai
        self.assertEqual(kwargs["env"]["SCRIBA_RELAUNCHED"], "1")  # retry no lock (#74)


@unittest.skipUnless(os.name == "posix", "flock/AF_UNIX são POSIX")
class PosixSingleInstanceTests(unittest.TestCase):
    """flock real: mesmo contrato do mutex do Windows (roda no CI Linux, #103)."""

    def setUp(self):
        import tempfile
        from scriba.plat import _posix
        self.posix = _posix
        self.tmp = tempfile.TemporaryDirectory()
        self.lock = str(Path(self.tmp.name) / "instance.lock")
        _posix._lock_file = None

    def tearDown(self):
        if self.posix._lock_file:
            self.posix._lock_file.close()
            self.posix._lock_file = None
        self.tmp.cleanup()

    def test_adquire_quando_livre(self):
        self.assertTrue(self.posix.single_instance(name=self.lock))
        self.assertIsNotNone(self.posix._lock_file)

    def test_segunda_instancia_falha_com_lock_preso(self):
        import fcntl
        held = open(self.lock, "w")
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)  # simula instância anterior
        try:
            self.assertFalse(self.posix.single_instance(retries=0, name=self.lock))
        finally:
            held.close()

    def test_retry_adquire_apos_liberacao(self):
        import fcntl
        import threading
        held = open(self.lock, "w")
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        threading.Timer(0.05, held.close).start()  # fechar o fd solta o flock
        self.assertTrue(self.posix.single_instance(retries=30, delay=0.02, name=self.lock))


if __name__ == "__main__":
    unittest.main()
