"""Testes de scriba.updates: parsing/comparação de versão + leitura do manifesto.

Rede (Gist) e git NÃO são tocados — só a lógica pura e o manifesto com _http_json
stubado. Roda sem dependências: python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import updates as up  # noqa: E402


def _bump(v: str) -> str:
    a, b, c = up._parse_ver(v)
    return f"{a}.{b}.{c + 1}"


class ParseVerTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(up._parse_ver("1.2.3"), (1, 2, 3))
        self.assertEqual(up._parse_ver("v0.2.0"), (0, 2, 0))
        self.assertEqual(up._parse_ver("ScribaDev v10.20.30 rc"), (10, 20, 30))

    def test_invalidos(self):
        self.assertIsNone(up._parse_ver("sem versao"))
        self.assertIsNone(up._parse_ver(None))
        self.assertIsNone(up._parse_ver("1.2"))  # incompleto


class IsNewerTests(unittest.TestCase):
    def test_maior(self):
        self.assertTrue(up._is_newer("0.2.0", "0.3.0"))
        self.assertTrue(up._is_newer("0.2.0", "v0.2.1"))
        self.assertTrue(up._is_newer("0.9.0", "0.10.0"))  # numérico, não lexical
        self.assertTrue(up._is_newer("1.0.0", "2.0.0"))

    def test_nao_maior(self):
        self.assertFalse(up._is_newer("0.2.0", "0.2.0"))  # igual
        self.assertFalse(up._is_newer("0.2.0", "0.1.9"))  # menor
        self.assertFalse(up._is_newer("0.10.0", "0.9.0"))  # numérico
        self.assertFalse(up._is_newer("0.2.0", None))
        self.assertFalse(up._is_newer(None, "0.3.0"))
        self.assertFalse(up._is_newer("0.2.0", "lixo"))


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self._orig = up._http_json

    def tearDown(self):
        up._http_json = self._orig

    def test_latest_info_e_version(self):
        up._http_json = lambda url, timeout: {"version": "9.9.9", "url": "http://x", "notes": "n"}
        self.assertEqual(up.latest_info()["url"], "http://x")
        self.assertEqual(up.latest_version(), "9.9.9")
        self.assertEqual(up.download_url(), "http://x")

    def test_manifesto_malformado_vira_none(self):
        up._http_json = lambda url, timeout: {"sem": "version"}
        self.assertIsNone(up.latest_info())
        self.assertIsNone(up.latest_version())
        self.assertEqual(up.download_url(), up.RELEASES_PAGE)  # fallback

    def test_rede_falha_vira_none(self):
        def boom(url, timeout):
            raise OSError("sem rede")
        up._http_json = boom
        self.assertIsNone(up.latest_info())
        self.assertIsNone(up.latest_version())

    def test_update_available(self):
        up._http_json = lambda url, timeout: {"version": _bump(up.__version__)}
        self.assertEqual(up.update_available(), _bump(up.__version__))
        up._http_json = lambda url, timeout: {"version": up.__version__}
        self.assertIsNone(up.update_available())  # igual → nada novo


class DescribeTests(unittest.TestCase):
    def test_na_tag(self):
        self.assertEqual(up._describe_to_str("v0.3.0", "v0.3.0"), "v0.3.0")

    def test_commits_depois_da_tag(self):
        self.assertEqual(up._describe_to_str("v0.3.0-3-g9cba847", "v0.3.0"),
                         "v0.3.0 · +3 · 9cba847")

    def test_modificado(self):
        self.assertEqual(up._describe_to_str("v0.3.0-3-g9cba847-dirty", "v0.3.0"),
                         "v0.3.0 · +3 · 9cba847 (modificado)")

    def test_so_hash_sem_tag(self):
        self.assertEqual(up._describe_to_str("9cba847", "v0.3.0"), "v0.3.0 · 9cba847")

    def test_vazio(self):
        self.assertEqual(up._describe_to_str("", "v0.3.0"), "v0.3.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
