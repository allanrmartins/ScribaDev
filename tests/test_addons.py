"""Addons da instalação congelada: pip in-process com saída CAPTURADA em logs/pip.log
(relato de campo: "pip retornou 2" sem pista nenhuma — o traceback ia p/ um stderr
inexistente no app sem console). pip mockado — determinístico, sem rede."""

import json
import os
import shutil
import sys
import tempfile
import time
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

    def test_remove_handlers_que_o_pip_pendura_no_root(self):
        """O pip in-process configura o PRÓPRIO logging (rich) no root logger e não
        desfaz — o handler órfão aponta pro pip.log já fechado e cada log do app
        depois do download virava '--- Logging error ---: I/O operation on closed
        file' (visto no E2E do #164)."""
        import logging

        orfao = logging.NullHandler()

        def fake_pip(args):
            logging.getLogger().addHandler(orfao)
            return 0

        ok, _ = self._run(fake_pip)
        self.assertTrue(ok)
        self.assertNotIn(orfao, logging.getLogger().handlers)

    def test_systemexit_do_pip_vira_rc(self):
        def fake_pip(args):
            raise SystemExit(1)

        ok, msg = self._run(fake_pip)
        self.assertFalse(ok)
        self.assertIn("pip retornou 1", msg)

    def test_repoe_handlers_que_o_pip_remove_do_root(self):
        """#167: 71 min de blackout TOTAL do scriba.log no caso real - o pip
        tirou os handlers do app do root e nada os repunha."""
        import logging

        nosso = logging.NullHandler()
        logging.getLogger().addHandler(nosso)
        try:
            def fake_pip(args):
                logging.getLogger().removeHandler(nosso)
                return 0

            ok, _ = self._run(fake_pip)
            self.assertTrue(ok)
            self.assertIn(nosso, logging.getLogger().handlers)  # voltou
        finally:
            logging.getLogger().removeHandler(nosso)


class InstallingMarkerTests(unittest.TestCase):
    """Marcador .installing (#167): o pipeline adia processamento enquanto a
    instalação de componentes reescreve o addons (pip --target --upgrade)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_mark_"))
        self._app0 = util.APP_DIR
        util.APP_DIR = self.tmp
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def tearDown(self):
        util.APP_DIR = self._app0

    def _write(self, payload: str) -> None:
        d = addons.addons_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / addons._INSTALLING_MARKER).write_text(payload, encoding="utf-8")

    def test_liga_desliga(self):
        self.assertFalse(addons.is_installing())
        addons.set_installing(True)
        self.assertTrue(addons.is_installing())     # PID desta suíte: vivo
        addons.set_installing(False)
        self.assertFalse(addons.is_installing())

    def test_orfao_de_crash_e_removido(self):
        # app morreu no meio da instalação: PID morto = marcador some na hora
        # (diferente do .lock de reunião - aqui a morte do dono ENCERRA a janela)
        self._write(json.dumps({"pid": 999999999, "started": time.time()}))
        self.assertFalse(addons.is_installing())
        self.assertFalse((addons.addons_dir() / addons._INSTALLING_MARKER).exists())

    def test_ilegivel_vale_por_precaucao(self):
        self._write("{quebrado")   # sendo escrito agora? melhor adiar do que quebrar
        self.assertTrue(addons.is_installing())

    def test_idade_acima_da_trava_nunca_vale(self):
        # PID vivo mas 13 h depois: quase certamente reciclado - destrava a fila
        self._write(json.dumps({"pid": os.getpid(),
                                "started": time.time() - 13 * 3600}))
        self.assertFalse(addons.is_installing())


class AbiDosAddonsTests(unittest.TestCase):
    """Guarda de ABI (#147): addons de `pip --target` valem só para o Python que os
    instalou. Sem esta guarda, reconstruir o app com outro minor (3.12 -> 3.14) faz o
    numpy do addons shadowar o do bundle e a TRANSCRIÇÃO para de importar."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_abi_"))
        self._app0 = util.APP_DIR
        util.APP_DIR = self.tmp
        self.d = addons.addons_dir()
        self.d.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def tearDown(self):
        util.APP_DIR = self._app0

    def test_abi_atual_e_do_interprete(self):
        self.assertEqual(addons.abi_atual(),
                         f"cpython-{sys.version_info.major}{sys.version_info.minor}")

    def test_le_o_carimbo_quando_existe(self):
        (self.d / addons._ABI_STAMP).write_text("cpython-312", encoding="utf-8")
        self.assertEqual(addons.abi_dos_addons(self.d), "cpython-312")

    def test_sonda_o_nome_do_so_sem_carimbo(self):
        """Pasta escrita por app antigo (sem carimbo): a tag vem do nome do .so."""
        so = self.d / "numpy" / "_core" / "_multiarray_umath.cpython-312-darwin.so"
        so.parent.mkdir(parents=True)
        so.write_bytes(b"")
        self.assertEqual(addons.abi_dos_addons(self.d), "cpython-312")

    def test_sem_pista_devolve_none(self):
        (self.d / "puro").mkdir()
        (self.d / "puro" / "__init__.py").write_text("", encoding="utf-8")
        self.assertIsNone(addons.abi_dos_addons(self.d))

    def test_bootstrap_ignora_addons_de_outra_abi(self):
        (self.d / addons._ABI_STAMP).write_text("cpython-312", encoding="utf-8")
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(addons, "abi_atual", return_value="cpython-314"), \
                mock.patch.object(sys, "path", list(sys.path)):
            self.assertFalse(addons.bootstrap())
            self.assertNotIn(str(self.d), sys.path)

    def test_bootstrap_aceita_addons_da_mesma_abi(self):
        (self.d / addons._ABI_STAMP).write_text(addons.abi_atual(), encoding="utf-8")
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "path", list(sys.path)):
            self.assertTrue(addons.bootstrap())
            self.assertIn(str(self.d), sys.path)

    def test_bootstrap_aceita_quando_a_abi_e_desconhecida(self):
        """Sem pista de ABI não há incompatibilidade a detectar — não regride o
        comportamento histórico (addons entra no sys.path)."""
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "path", list(sys.path)):
            self.assertTrue(addons.bootstrap())
            self.assertIn(str(self.d), sys.path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
