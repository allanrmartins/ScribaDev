"""Testes de _csv_merge: presets da aba Detecção (#21). Só a função pura (o helper
migrou para scriba.qt.settings_ui no corte para Qt, #53)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.qt.settings_ui import _csv_merge  # noqa: E402


class CsvMergeTests(unittest.TestCase):
    def test_adiciona_item_novo(self):
        self.assertEqual(_csv_merge("teams, zoom", ["webex"]), "teams, zoom, webex")

    def test_nao_duplica_case_insensitive(self):
        self.assertEqual(_csv_merge("Teams", ["teams"]), "Teams")

    def test_partindo_de_vazio(self):
        self.assertEqual(_csv_merge("", ["teams"]), "teams")

    def test_normaliza_para_o_formato_consumido(self):
        # detector.*_from faz split(',')+strip; o merge devolve 'a, b, c'
        self.assertEqual(_csv_merge(" a ,b ", ["c"]), "a, b, c")

    def test_varios_de_uma_vez_sem_duplicar(self):
        self.assertEqual(_csv_merge("teams", ["zoom", "teams", "webex"]), "teams, zoom, webex")


if __name__ == "__main__":
    unittest.main(verbosity=2)
