"""Testes de scriba.overlay: clamp de posição e state.json atômico (#17)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import overlay, util  # noqa: E402


class PosInBoundsTests(unittest.TestCase):
    def test_dentro_do_primario(self):
        self.assertTrue(overlay._pos_in_bounds(100, 100, 0, 0, 1920, 1080))

    def test_fora_negativo(self):
        self.assertFalse(overlay._pos_in_bounds(-5000, -5000, 0, 0, 1920, 1080))

    def test_monitor_desconectado(self):
        # x=3000 num desktop de 1920 -> coordenada de tela que sumiu
        self.assertFalse(overlay._pos_in_bounds(3000, 100, 0, 0, 1920, 1080))

    def test_monitor_secundario_conectado(self):
        # mesmo x=2000, mas o desktop virtual tem 3840 de largura -> valido
        self.assertTrue(overlay._pos_in_bounds(2000, 100, 0, 0, 3840, 1080))

    def test_origem_virtual_negativa(self):
        self.assertTrue(overlay._pos_in_bounds(-1900, 100, -1920, 0, 3840, 1080))


class SavePosTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_st_"))
        util.STATE_PATH = d / "state.json"

    def test_merge_preserva_outras_chaves(self):
        util.STATE_PATH.write_text(json.dumps({"other": "keep"}), encoding="utf-8")
        overlay._save_pos(50, 60)
        data = json.loads(util.STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(data["other"], "keep")
        self.assertEqual(data["overlay_pos"], [50, 60])

    def test_nao_deixa_tmp(self):
        overlay._save_pos(1, 2)
        self.assertFalse(util.STATE_PATH.with_name("state.json.tmp").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
