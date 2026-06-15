"""Testes de scriba.speakers (#1): store + matching de voz por cosseno.

Não precisa de pyannote/torch — só numpy (dependência transitiva do projeto).
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import speakers, util  # noqa: E402


class SpeakersStoreTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_spk_"))
        util.APP_DIR = d
        util.LOGS_DIR = d / "logs"
        speakers.STORE_PATH = d / "speakers.json"

    def test_cosine_basico(self):
        self.assertAlmostEqual(speakers._cosine([1, 0, 0], [1, 0, 0]), 1.0, places=5)
        self.assertAlmostEqual(speakers._cosine([1, 0, 0], [0, 1, 0]), 0.0, places=5)
        self.assertAlmostEqual(speakers._cosine([1, 0], [-1, 0]), -1.0, places=5)
        self.assertEqual(speakers._cosine([0, 0], [1, 0]), 0.0)  # vetor nulo não quebra

    def test_enroll_cria_e_reforca_incremental(self):
        speakers.enroll("Marcelo", [1.0, 0.0, 0.0])
        st = speakers.load_store()
        self.assertEqual(len(st), 1)
        self.assertEqual((st[0]["name"], st[0]["n"]), ("Marcelo", 1))
        # reforço: case-insensitive, funde no centroide (média) e incrementa n
        speakers.enroll("marcelo", [0.0, 1.0, 0.0])
        st = speakers.load_store()
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0]["n"], 2)
        self.assertEqual(st[0]["name"], "marcelo")  # normaliza p/ a última grafia
        self.assertAlmostEqual(st[0]["embedding"][0], 0.5, places=5)
        self.assertAlmostEqual(st[0]["embedding"][1], 0.5, places=5)

    def test_match_acima_e_abaixo_do_limiar(self):
        speakers.enroll("Suzi", [1.0, 0.0, 0.0])
        speakers.enroll("Marcelo", [0.0, 1.0, 0.0])
        name, score = speakers.match([0.9, 0.1, 0.0], threshold=0.55)
        self.assertEqual(name, "Suzi")
        self.assertGreater(score, 0.55)
        # voz ortogonal a todas as cadastradas → sem match (fica Participante N)
        name, _ = speakers.match([0.0, 0.0, 1.0], threshold=0.55)
        self.assertIsNone(name)

    def test_match_store_vazio(self):
        name, score = speakers.match([1.0, 0.0])
        self.assertIsNone(name)
        self.assertEqual(score, 0.0)

    def test_enroll_invalido_nao_grava(self):
        speakers.enroll("", [1.0, 0.0])   # sem nome
        speakers.enroll("X", [])           # sem embedding
        self.assertEqual(speakers.load_store(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
