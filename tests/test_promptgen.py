"""Testes de scriba.promptgen: state.json do wizard agora é atômico + merge
(straggler da campanha de escrita atômica)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import promptgen, util  # noqa: E402


class WizardStateTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_wz_"))
        util.APP_DIR = d
        util.LOGS_DIR = d / "logs"
        util.STATE_PATH = d / "state.json"

    def test_mark_wizard_done_preserva_outras_chaves(self):
        util.STATE_PATH.write_text(json.dumps({"overlay_pos": [9, 9]}), encoding="utf-8")
        promptgen.mark_wizard_done()
        data = json.loads(util.STATE_PATH.read_text(encoding="utf-8"))
        self.assertTrue(data["wizard_done"])
        self.assertEqual(data["overlay_pos"], [9, 9])  # não atropela a posição da pílula
        self.assertFalse(util.STATE_PATH.with_name("state.json.tmp").exists())

    def test_wizard_done_round_trip(self):
        self.assertFalse(promptgen.wizard_done())
        promptgen.mark_wizard_done()
        self.assertTrue(promptgen.wizard_done())


if __name__ == "__main__":
    unittest.main(verbosity=2)
