"""Addons da instalação congelada: pip in-process com saída CAPTURADA em logs/pip.log
(relato de campo: "pip retornou 2" sem pista nenhuma — o traceback ia p/ um stderr
inexistente no app sem console). pip mockado — determinístico, sem rede."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import addons, util  # noqa: E402


class InstallToAddonsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_addons_"))
        self._app0, self._logs0 = util.APP_DIR, util.LOGS_DIR
        util.APP_DIR = self.tmp / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def tearDown(self):
        util.APP_DIR, util.LOGS_DIR = self._app0, self._logs0

    def _run(self, fake_pip, packages=("torch",)):
        with mock.patch("pip._internal.cli.main.main", side_effect=fake_pip):
            return addons.install_to_addons(list(packages), progress=lambda _l: None)

    def test_saida_do_pip_vai_para_o_pip_log(self):
        def fake_pip(args):
            print("Collecting torch")          # stdout do pip → pip.log, não o void
            print("ERROR: explodiu feio", file=sys.stderr)
            return 2

        ok, msg = self._run(fake_pip)
        self.assertFalse(ok)
        logged = addons.pip_log_path().read_text(encoding="utf-8")
        self.assertIn("Collecting torch", logged)
        self.assertIn("explodiu feio", logged)
        # a mensagem aponta o arquivo e traz a última linha de ERRO
        self.assertIn(str(addons.pip_log_path()), msg)
        self.assertIn("explodiu feio", msg)

    def test_streams_nulos_nao_quebram(self):
        """App windowed (exe sem console): sys.stdout/stderr podem ser None — o
        redirect p/ o pip.log tem de blindar o print do pip mesmo assim."""
        def fake_pip(args):
            print("instalando…")
            return 0

        out0, err0 = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = None
        try:
            ok, msg = self._run(fake_pip)
        finally:
            sys.stdout, sys.stderr = out0, err0
        self.assertTrue(ok)
        self.assertIn("instalando…", addons.pip_log_path().read_text(encoding="utf-8"))

    def test_flags_de_blindagem_do_congelado(self):
        """--only-binary=:all: é obrigatório: um build de sdist rodaria sys.executable,
        que no bundle é o PRÓPRIO app — não um Python."""
        seen = {}

        def fake_pip(args):
            seen["args"] = args
            return 0

        ok, _ = self._run(fake_pip, packages=("torch", "pyannote.audio>=4,<5"))
        self.assertTrue(ok)
        self.assertIn("--only-binary=:all:", seen["args"])
        self.assertIn("--no-input", seen["args"])
        self.assertIn("torch", seen["args"])

    def test_systemexit_do_pip_vira_rc(self):
        def fake_pip(args):
            raise SystemExit(1)

        ok, msg = self._run(fake_pip)
        self.assertFalse(ok)
        self.assertIn("pip retornou 1", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
