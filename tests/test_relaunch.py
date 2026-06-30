"""Teste de ScribaApp.relaunch — auto-restart do update (#19).

Regressão do bug em que o app "não reiniciava sozinho": `Path` não estava no escopo
do método e o `Path(sys.executable)` (fora do try) estourava NameError, engolido pelo
handler de callback do Tk — o relançamento nunca era agendado e o quit nunca pedido.

Nada real roda aqui: subprocess, messagebox, os._exit e threading.Timer são stubados,
então o teste não abre janela, não relança processo nem encerra o runner.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.main import ScribaApp  # noqa: E402


class RelaunchTests(unittest.TestCase):
    def _app(self):
        # __new__ sem __init__: não cria Tk root nem threads; só queremos o método
        app = ScribaApp.__new__(ScribaApp)
        app.request_quit = mock.Mock()
        return app

    def test_agenda_relancamento_avisa_e_forca_saida(self):
        app = self._app()
        with mock.patch("subprocess.Popen") as popen, \
                mock.patch("tkinter.messagebox.showinfo") as info, \
                mock.patch("tkinter.messagebox.showwarning") as warn, \
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
                mock.patch("tkinter.messagebox.showinfo"), \
                mock.patch("tkinter.messagebox.showwarning") as warn, \
                mock.patch("os._exit") as oexit, \
                mock.patch("threading.Timer") as timer, \
                mock.patch("scriba.main.log"):
            app.relaunch()

            # não conseguiu agendar -> avisa e NÃO encerra (não deixa o usuário sem app)
            self.assertTrue(warn.called)
            app.request_quit.assert_not_called()
            self.assertFalse(timer.called)
            oexit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
