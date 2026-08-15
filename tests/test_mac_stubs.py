"""M1 do port macOS (épico #104, docs/port-mac.md): tema por SO, textos do template
da config, segredos no Keychain e so_nome.

Mesma convenção do test_stubs_posix: os guards checam sys.platform em TEMPO DE
CHAMADA, então os ramos darwin rodam em qualquer host patchando sys.platform.
Exceção: os hints _H da config são computados no IMPORT (como o wintitles) — os
testes de template recarregam o módulo sob o sys.platform desejado.
"""

import importlib
import subprocess as subprocess_mod
import sys
import unittest
from unittest import mock

from scriba import config, util
from scriba.qt import theme


def _darwin():
    return mock.patch.object(sys, "platform", "darwin")


def _linux():
    return mock.patch.object(sys, "platform", "linux")


def _sem_qapp():
    from PySide6.QtGui import QGuiApplication

    return mock.patch.object(QGuiApplication, "instance", staticmethod(lambda: None))


class TestTemaDarwin(unittest.TestCase):
    def test_defaults_read_modo_escuro(self):
        # AppleInterfaceStyle existe (rc 0) = modo escuro
        with _darwin(), _sem_qapp(), mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0)
            self.assertTrue(theme._os_prefers_dark())
        self.assertEqual(run.call_args[0][0][:3], ["defaults", "read", "-g"])

    def test_defaults_read_modo_claro(self):
        # a chave só existe no escuro: rc != 0 = modo claro (era o bug: caía no True)
        with _darwin(), _sem_qapp(), mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1)
            self.assertFalse(theme._os_prefers_dark())

    def test_falha_total_mantem_escuro(self):
        with _darwin(), _sem_qapp(), mock.patch("subprocess.run", side_effect=OSError("boom")):
            self.assertTrue(theme._os_prefers_dark())


class TestTemplateConfigPorSO(unittest.TestCase):
    """DEFAULT_CONFIG e o template do save() falam a língua do SO. Os hints são
    resolvidos no import ⇒ reload sob sys.platform patchado, com restauração."""

    def _render(self, platform: str) -> str:
        with mock.patch.object(sys, "platform", platform):
            importlib.reload(config)
            return config.DEFAULT_CONFIG

    def tearDown(self):
        importlib.reload(config)  # restaura os hints do host real

    def test_darwin_sem_caminhos_windows(self):
        text = self._render("darwin")
        self.assertIn("Application Support/ScribaDev/Notas", text)
        self.assertIn("Application Support/ScribaDev/gravacoes", text)
        self.assertIn("fica no Keychain", text)
        self.assertNotIn("%LOCALAPPDATA%", text)
        self.assertNotIn("C:\\temp", text)
        self.assertNotIn("do Windows", text)

    def test_win32_texto_historico_intacto(self):
        text = self._render("win32")
        self.assertIn(r"Vazio = %LOCALAPPDATA%\ScribaDev\Notas", text)
        self.assertIn(r"Vazio = C:\temp\scribadev\gravacoes", text)
        self.assertIn("registro de uso do microfone do\n# Windows", text)
        self.assertIn("fica cifrada (DPAPI)", text)
        self.assertNotIn("Keychain", text)

    def test_template_e_toml_valido_nos_tres_sos(self):
        import tomllib

        for platform in ("win32", "darwin", "linux"):
            tomllib.loads(self._render(platform))

    def test_save_template_darwin(self):
        import tomllib

        text = self._render("darwin")
        cfg = config._build(tomllib.loads(text))
        rendered = {}

        def capture(path, content):
            rendered["text"] = content

        with mock.patch.object(sys, "platform", "darwin"), \
                mock.patch.object(config.util, "ensure_app_dirs"), \
                mock.patch.object(config.util, "atomic_write_text", capture), \
                mock.patch.object(config.util.CONFIG_PATH.__class__, "exists", lambda self: False):
            config.save(cfg)
        self.assertIn("Application Support/ScribaDev/Notas", rendered["text"])
        self.assertNotIn("%LOCALAPPDATA%", rendered["text"])
        self.assertNotIn("do Windows", rendered["text"])


class TestKeychain(unittest.TestCase):
    def test_fora_do_darwin_devolve_none(self):
        with _linux():
            self.assertIsNone(util.keychain_store("conta", "segredo"))
            self.assertIsNone(util.keychain_lookup("keychain:conta"))
            self.assertFalse(util.keychain_ok())

    def test_store_segredo_vai_por_stdin_nunca_argv(self):
        with _darwin(), mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0)
            token = util.keychain_store("summary.openai_api_key", "sk-SEGREDO123")
        self.assertEqual(token, "keychain:summary.openai_api_key")
        argv = run.call_args[0][0]
        self.assertEqual(argv, ["/usr/bin/security", "-i"])
        self.assertNotIn("sk-SEGREDO123", " ".join(argv))
        self.assertIn(b"sk-SEGREDO123", run.call_args[1]["input"])

    def test_store_recusa_texto_arriscado(self):
        # aspas/controle poderiam quebrar o tokenizer do security -i: degrada p/ plaintext
        with _darwin(), mock.patch("subprocess.run") as run:
            self.assertIsNone(util.keychain_store("conta", 'se"gredo'))
            self.assertIsNone(util.keychain_store("conta", "seg\nredo"))
            self.assertIsNone(util.keychain_store('con"ta', "segredo"))
        run.assert_not_called()

    def test_store_falha_devolve_none(self):
        with _darwin(), mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=51)  # chaveiro trancado etc.
            self.assertIsNone(util.keychain_store("conta", "segredo"))

    def test_lookup_roundtrip(self):
        with _darwin(), mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=b"sk-SEGREDO123\n")
            self.assertEqual(util.keychain_lookup("keychain:conta"), "sk-SEGREDO123")
            run.return_value = mock.Mock(returncode=44)  # item não existe
            self.assertIsNone(util.keychain_lookup("keychain:conta"))

    def test_maybe_protect_darwin_usa_keychain(self):
        with _darwin(), mock.patch.object(config.util, "keychain_store",
                                          return_value="keychain:x") as ks:
            self.assertEqual(config._maybe_protect("segredo", "x"), "keychain:x")
        ks.assert_called_once_with("x", "segredo")

    def test_maybe_protect_darwin_sem_keychain_degrada_plaintext(self):
        with _darwin(), mock.patch.object(config.util, "keychain_store", return_value=None):
            self.assertEqual(config._maybe_protect("segredo", "x"), "segredo")

    def test_maybe_protect_token_existente_passa_direto(self):
        with _darwin():
            self.assertEqual(config._maybe_protect("keychain:x", "x"), "keychain:x")
            self.assertEqual(config._maybe_protect("dpapi:abc", "x"), "dpapi:abc")
            self.assertEqual(config._maybe_protect("", "x"), "")

    def test_maybe_unprotect_resolve_keychain(self):
        with mock.patch.object(config.util, "keychain_lookup", return_value="segredo"):
            self.assertEqual(config._maybe_unprotect("keychain:x"), "segredo")
        with mock.patch.object(config.util, "keychain_lookup", return_value=None):
            # item removido/chaveiro trancado: chave inutilizável — mesma regra da DPAPI
            self.assertEqual(config._maybe_unprotect("keychain:x"), "")


class TestSoNome(unittest.TestCase):
    def test_por_plataforma(self):
        with mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(util.so_nome(), "Windows")
        with _darwin():
            self.assertEqual(util.so_nome(), "macOS")
        with _linux():
            self.assertEqual(util.so_nome(), "Linux")


if __name__ == "__main__":
    unittest.main()
