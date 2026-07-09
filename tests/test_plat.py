"""#97: camada de plataforma — paths por SO e despacho do backend.

Os dois backends são importáveis em qualquer SO (só stdlib), então o POSIX é
testado aqui mesmo no Windows, direto em scriba.plat._posix.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from scriba import plat
from scriba.plat import _posix, _win

REPO = Path(__file__).resolve().parent.parent


class TestBackendWindows(unittest.TestCase):
    def test_app_data_dir_usa_localappdata(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}):
            self.assertEqual(
                _win.app_data_dir(), Path(r"C:\Users\x\AppData\Local") / "ScribaDev"
            )

    def test_app_data_dir_fallback_home(self):
        env = {k: v for k, v in os.environ.items() if k != "LOCALAPPDATA"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(_win.app_data_dir(), Path.home() / "ScribaDev")

    def test_default_recordings_dir_historico(self):
        # invariante do épico #104: comportamento do Windows não muda
        self.assertEqual(_win.default_recordings_dir(), Path(r"C:\temp\scribadev\gravacoes"))


class TestBackendPosix(unittest.TestCase):
    def test_linux_respeita_xdg_data_home(self):
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/xdg"}):
            self.assertEqual(_posix.app_data_dir(), Path("/tmp/xdg") / "ScribaDev")

    def test_linux_fallback_local_share(self):
        # XDG_DATA_HOME vazio = ausente (string vazia é falsy no get)
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": ""}):
            self.assertEqual(
                _posix.app_data_dir(), Path.home() / ".local" / "share" / "ScribaDev"
            )

    def test_macos_application_support(self):
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual(
                _posix.app_data_dir(),
                Path.home() / "Library" / "Application Support" / "ScribaDev",
            )

    def test_default_recordings_sob_app_data(self):
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/xdg"}):
            self.assertEqual(
                _posix.default_recordings_dir(), Path("/tmp/xdg") / "ScribaDev" / "gravacoes"
            )


class TestHasNvidiaGpu(unittest.TestCase):
    def test_win_devolve_bool(self):
        # sonda real no runner (com ou sem GPU): o contrato é não levantar
        self.assertIsInstance(_win.has_nvidia_gpu(), bool)

    def test_posix_linux_devolve_bool(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertIsInstance(_posix.has_nvidia_gpu(), bool)

    def test_posix_macos_sempre_false(self):
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertIs(_posix.has_nvidia_gpu(), False)


class TestDespacho(unittest.TestCase):
    def test_backend_casa_com_o_so_atual(self):
        esperado = _win if sys.platform == "win32" else _posix
        self.assertIs(plat.app_data_dir, esperado.app_data_dir)
        self.assertIs(plat.default_recordings_dir, esperado.default_recordings_dir)
        self.assertIs(plat.has_nvidia_gpu, esperado.has_nvidia_gpu)
        self.assertIs(plat.open_path, esperado.open_path)

    def test_util_app_dir_vem_da_camada(self):
        # em subprocesso limpo: outros testes da suíte patcham util.APP_DIR
        # (isolamento do índice) e a ordem de execução não pode influenciar
        code = (
            "from scriba import plat, util; "
            "assert util.APP_DIR == plat.app_data_dir(), (util.APP_DIR, plat.app_data_dir()); "
            "print('OK')"
        )
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", code],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(REPO), timeout=60,
        )
        self.assertEqual(proc.returncode, 0, msg=f"stderr:\n{proc.stderr}")
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
