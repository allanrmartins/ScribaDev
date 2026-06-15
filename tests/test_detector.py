"""Testes de scriba.detector._clean_meeting_title (nome da reunião pelo título da janela)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.detector import _clean_meeting_title  # noqa: E402


class CleanMeetingTitleTests(unittest.TestCase):
    def test_teams_chat(self):
        self.assertEqual(
            _clean_meeting_title("Chat | Daily - Harald Projeto Horizon | Microsoft Teams"),
            "Daily - Harald Projeto Horizon",
        )

    def test_teams_reuniao_simples(self):
        self.assertEqual(_clean_meeting_title("Alinhamento estoque | Microsoft Teams"), "Alinhamento estoque")

    def test_teams_generico_vira_vazio(self):
        self.assertEqual(_clean_meeting_title("Microsoft Teams"), "")
        self.assertEqual(_clean_meeting_title("Chat | Microsoft Teams"), "")

    def test_contador_naolidas(self):
        self.assertEqual(_clean_meeting_title("(12) Chat | Projeto X | Microsoft Teams"), "Projeto X")

    def test_navegador_sufixo(self):
        self.assertEqual(_clean_meeting_title("Reunião de equipe - Google Chrome"), "Reunião de equipe")
        self.assertEqual(_clean_meeting_title("Sprint review - Microsoft Edge"), "Sprint review")

    def test_meet_prefixo(self):
        self.assertEqual(_clean_meeting_title("Meet - Daily SAP"), "Daily SAP")

    def test_vazio_e_so_app(self):
        self.assertEqual(_clean_meeting_title(""), "")
        self.assertEqual(_clean_meeting_title("   "), "")
        self.assertEqual(_clean_meeting_title("Zoom"), "")


if __name__ == "__main__":
    unittest.main()
