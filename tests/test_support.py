"""Reportar erro no GitHub (scriba.support): URL pré-preenchida + zip best-effort.
Tudo mockado — nada de rede/navegador/Explorer nos testes."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import __version__, support  # noqa: E402


class IssueUrlTests(unittest.TestCase):
    def test_url_pre_preenchida(self):
        url = support.issue_url("titulo x", "corpo com acento é & = fim")
        self.assertTrue(url.startswith(support.REPO_ISSUES_NEW + "?"))
        q = parse_qs(urlparse(url).query)
        self.assertEqual(q["title"], ["titulo x"])
        self.assertEqual(q["body"], ["corpo com acento é & = fim"])   # round-trip fiel


class BuildReportTests(unittest.TestCase):
    def _q(self, url):
        return parse_qs(urlparse(url).query)

    def test_com_zip_o_corpo_pede_o_arraste(self):
        z = Path(tempfile.mkdtemp(prefix="scriba_sup_")) / "scribadev-diagnostico-x.zip"
        z.write_bytes(b"zip")
        with mock.patch("scriba.diagnostics.export_zip", return_value=z):
            zip_path, url = support.build_report("Download falhou", "pip retornou 2")
        self.assertEqual(zip_path, z)
        body = self._q(url)["body"][0]
        self.assertIn(z.name, body)                    # pede o arraste do arquivo certo
        self.assertIn("pip retornou 2", body)          # detalhe do erro no corpo
        self.assertIn(f"v{__version__}", body)         # ambiente identificado
        self.assertIn("já saem ofuscados", body)       # aviso de privacidade presente
        self.assertIn("[erro] Download falhou", self._q(url)["title"][0])

    def test_detalhe_do_corpo_e_ofuscado(self):
        from scriba import diagnostics

        s = diagnostics.Scrubber()
        s.add("Coruripe", "cliente")
        with mock.patch("scriba.diagnostics.export_zip", side_effect=OSError("x")), \
                mock.patch("scriba.diagnostics.build_scrubber", return_value=s):
            _z, url = support.build_report("ctx", "erro processando Coruripe")
        body = self._q(url)["body"][0]
        self.assertNotIn("Coruripe", body)
        self.assertIn("[cliente-1]", body)

    def test_falha_na_ofuscacao_omite_o_detalhe(self):
        # privacidade primeiro: detalhe que não deu para ofuscar NÃO sai cru
        with mock.patch("scriba.diagnostics.export_zip", side_effect=OSError("x")), \
                mock.patch("scriba.diagnostics.build_scrubber",
                           side_effect=RuntimeError("boom")):
            _z, url = support.build_report("ctx", "segredo com Nome Sensivel")
        body = self._q(url)["body"][0]
        self.assertNotIn("Nome Sensivel", body)
        self.assertIn("detalhe omitido", body)

    def test_sem_zip_a_issue_sai_mesmo_assim(self):
        with mock.patch("scriba.diagnostics.export_zip", side_effect=OSError("disco")):
            zip_path, url = support.build_report("Download falhou", "x")
        self.assertIsNone(zip_path)
        self.assertIn("não pôde ser gerado", self._q(url)["body"][0])

    def test_detalhe_gigante_e_truncado(self):
        with mock.patch("scriba.diagnostics.export_zip", side_effect=OSError("x")):
            _z, url = support.build_report("ctx", "L" * 5000)
        self.assertLess(len(self._q(url)["body"][0]), 2000)


class OpenReportTests(unittest.TestCase):
    def test_abre_navegador_e_seleciona_o_zip(self):
        z = Path(tempfile.mkdtemp(prefix="scriba_sup_")) / "d.zip"
        z.write_bytes(b"zip")
        with mock.patch("scriba.diagnostics.export_zip", return_value=z), \
                mock.patch("scriba.util.reveal_path") as reveal, \
                mock.patch("scriba.support.webbrowser.open") as wb:
            out = support.open_report("ctx", "detalhe")
        self.assertEqual(out, z)
        reveal.assert_called_once_with(z)
        self.assertTrue(wb.call_args[0][0].startswith(support.REPO_ISSUES_NEW))

    def test_sem_zip_ainda_abre_o_navegador(self):
        with mock.patch("scriba.diagnostics.export_zip", side_effect=OSError("x")), \
                mock.patch("scriba.util.reveal_path") as reveal, \
                mock.patch("scriba.support.webbrowser.open") as wb:
            out = support.open_report("ctx", "d")
        self.assertIsNone(out)
        reveal.assert_not_called()
        wb.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
