"""#97: camada de plataforma — paths por SO e despacho do backend.

Os dois backends são importáveis em qualquer SO (só stdlib), então o POSIX é
testado aqui mesmo no Windows, direto em scriba.plat._posix.
"""

import os
import subprocess
import sys
import tempfile
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
    @unittest.skipUnless(sys.platform == "win32", "sonda o nvcuda.dll real (WinDLL)")
    def test_win_devolve_bool(self):
        # sonda real no runner (com ou sem GPU): o contrato é não levantar
        self.assertIsInstance(_win.has_nvidia_gpu(), bool)

    def test_posix_linux_devolve_bool(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertIsInstance(_posix.has_nvidia_gpu(), bool)

    def test_posix_macos_sempre_false(self):
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertIs(_posix.has_nvidia_gpu(), False)


class TestEnsureUserPath(unittest.TestCase):
    """PATH do usuário no app de GUI (bug do DMG: `claude`/`ffmpeg` "não instalados").

    App lançado do Finder/Dock/LaunchAgent herda do launchd só
    /usr/bin:/bin:/usr/sbin:/sbin — sem Homebrew e sem ~/.local/bin.
    """

    # montado com os.pathsep: o código divide o PATH pelo separador DA PLATAFORMA —
    # com ":" literal os testes passavam no macOS e quebravam no Windows (CI 3 SOs)
    _LAUNCHD_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
    LAUNCHD = os.pathsep.join(_LAUNCHD_DIRS)

    def test_acrescenta_dirs_do_usuario_que_existem(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_bin_"))
        with mock.patch.dict(os.environ, {"PATH": self.LAUNCHD}), \
                mock.patch.object(_posix, "_DIRS_EXTRA", (str(d),)), \
                mock.patch.object(_posix, "_path_do_shell", return_value=[]):
            novo = _posix.ensure_user_path()
        self.assertEqual(tuple(novo.split(os.pathsep)[:4]), self._LAUNCHD_DIRS)
        self.assertIn(str(d), novo.split(os.pathsep))

    def test_ignora_dir_inexistente(self):
        fake = "/nao/existe/scriba-bin"
        with mock.patch.dict(os.environ, {"PATH": self.LAUNCHD}), \
                mock.patch.object(_posix, "_DIRS_EXTRA", (fake,)), \
                mock.patch.object(_posix, "_path_do_shell", return_value=[]):
            novo = _posix.ensure_user_path()
        self.assertNotIn(fake, novo.split(os.pathsep))

    def test_nao_duplica_nem_reordena_o_que_ja_estava(self):
        """Rodando da CLI o PATH do shell já está certo: só append, sem reordenar."""
        d = Path(tempfile.mkdtemp(prefix="scriba_bin_"))
        antes = f"{d}{os.pathsep}/usr/bin{os.pathsep}/bin"
        with mock.patch.dict(os.environ, {"PATH": antes}), \
                mock.patch.object(_posix, "_DIRS_EXTRA", (str(d),)), \
                mock.patch.object(_posix, "_path_do_shell", return_value=[]):
            novo = _posix.ensure_user_path()
        self.assertEqual(novo, antes)

    def test_so_pergunta_ao_shell_quando_o_path_e_do_launchd(self):
        """Spawnar shell de login é caro (~centenas de ms): na CLI não deve acontecer."""
        with mock.patch.object(_posix, "_path_do_shell") as m:
            path_de_shell = os.pathsep.join(("/opt/homebrew/bin", "/usr/bin"))
            with mock.patch.dict(os.environ, {"PATH": path_de_shell}):
                _posix.ensure_user_path()
            m.assert_not_called()
            with mock.patch.dict(os.environ, {"PATH": self.LAUNCHD}):
                _posix.ensure_user_path()
            m.assert_called_once()

    def test_path_do_shell_le_entre_os_marcadores(self):
        ini, fim = _posix._MARCA
        saida = f"ruído do rc\n{ini}/a{os.pathsep}/b{fim}"
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout=saida)):
            self.assertEqual(_posix._path_do_shell(), ["/a", "/b"])

    def test_path_do_shell_tolera_falha(self):
        for erro in (OSError("boom"), subprocess.TimeoutExpired("zsh", 5)):
            with self.subTest(erro=type(erro).__name__):
                with mock.patch("subprocess.run", side_effect=erro):
                    self.assertEqual(_posix._path_do_shell(), [])

    def test_path_do_shell_sem_marcador_devolve_vazio(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout="nada aqui")):
            self.assertEqual(_posix._path_do_shell(), [])

    def test_windows_e_no_op(self):
        """Regra de ouro do #104: o comportamento do Windows não muda."""
        with mock.patch.dict(os.environ, {"PATH": r"C:\Windows"}):
            self.assertEqual(_win.ensure_user_path(), r"C:\Windows")
            self.assertEqual(os.environ["PATH"], r"C:\Windows")


class TestDespacho(unittest.TestCase):
    def test_backend_casa_com_o_so_atual(self):
        esperado = _win if sys.platform == "win32" else _posix
        self.assertIs(plat.app_data_dir, esperado.app_data_dir)
        self.assertIs(plat.default_recordings_dir, esperado.default_recordings_dir)
        self.assertIs(plat.ensure_user_path, esperado.ensure_user_path)
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
