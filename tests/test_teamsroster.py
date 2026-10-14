"""Testes de scriba.teamsroster: quem abriu o mic na call do Teams (sugestão do nº de vozes).

Os textos seguem o formato que a janela da call expõe na acessibilidade (medido numa
call real em 2026-10-14), com nomes fictícios.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import teamsroster  # noqa: E402
from scriba.teamsroster import RosterTracker, RosterWatch, parse, parse_profile, parse_total  # noqa: E402

_CTX = ", Context menu is available"


def tile(name: str, *flags: str, external: bool = False) -> str:
    return name + (" External unfamiliar" if external else "") + "".join(f", {f}" for f in flags) + _CTX


class ParseTests(unittest.TestCase):
    def test_bloco_de_video(self):
        self.assertEqual(parse(tile("Ana Lima", "video is on", "Has control of production tools")),
                         ("Ana Lima", "open"))
        self.assertEqual(parse(tile("Bruno Reis", "Has control of production tools", "muted", external=True)),
                         ("Bruno Reis", "muted"))
        self.assertEqual(parse(tile("Carla Dias", "hand raised", external=True)), ("Carla Dias", "open"))

    def test_painel_people(self):
        self.assertEqual(parse("Ana Lima, Muted"), ("Ana Lima", "muted"))
        self.assertEqual(parse("Bruno Reis External unfamiliar, Organizer, Unmuted"), ("Bruno Reis", "open"))
        self.assertEqual(parse("Carla Dias External unfamiliar, Hand raised position 1, Unmuted"),
                         ("Carla Dias", "open"))

    def test_quem_fala(self):
        self.assertEqual(parse("Ana Lima is speaking"), ("Ana Lima", "speaking"))
        self.assertIsNone(parse("Nobody is speaking"))

    def test_textos_que_nao_sao_participante(self):
        for t in ("Content shared by Ana Lima" + _CTX, "In this meeting, 12 total", "People",
                  "Meeting controls", "", "Ana Lima"):
            self.assertIsNone(parse(t), t)

    def test_total_e_perfil(self):
        self.assertEqual(parse_total("In this meeting, 56 total Mute all"), 56)
        self.assertIsNone(parse_total("People"))
        self.assertEqual(parse_profile("Profile picture of Ana Lima."), "Ana Lima")
        self.assertIsNone(parse_profile("Your profile, status Available"))


class TrackerTests(unittest.TestCase):
    def feed(self, tracker: RosterTracker, texts: list[str], seconds: float, start: float = 0.0) -> float:
        """Repete a mesma tela a cada 5 s por `seconds` segundos."""
        t = start
        while t <= start + seconds:
            tracker.feed(texts, t)
            t += teamsroster.SAMPLE_SECONDS
        return t

    def test_call_grande_conta_so_quem_abriu_o_mic(self):
        tr = RosterTracker()
        tela = [tile("Ana Lima", "video is on")] + [tile(f"Ouvinte {i}", "muted") for i in range(30)]
        self.feed(tr, tela, 60)
        self.assertEqual(tr.speakers(), ["Ana Lima"])

    def test_mic_aberto_por_um_instante_nao_conta(self):
        tr = RosterTracker()
        t = self.feed(tr, [tile("Ana Lima", "muted")], 20)
        tr.feed([tile("Ana Lima")], t)               # abriu...
        tr.feed([tile("Ana Lima", "muted")], t + 5)  # ...e fechou 5 s depois
        self.assertEqual(tr.speakers(), [])

    def test_quem_aparece_falando_conta_na_hora(self):
        tr = RosterTracker()
        tr.feed([tile("Ana Lima", "muted"), "Bruno Reis is speaking"], 0.0)
        self.assertEqual(tr.speakers(), ["Bruno Reis"])

    def test_dono_da_conta_nao_e_voz_do_loopback(self):
        tr = RosterTracker()
        tr.self_name = "Ana Lima"
        self.feed(tr, ["Ana Lima, Unmuted", "Bruno Reis, Unmuted"], 30)
        self.assertEqual(tr.speakers(), ["Bruno Reis"])

    def test_bot_de_gravacao_nao_conta(self):
        tr = RosterTracker()
        self.feed(tr, ["Robo.ai Notetaker Ana Lima, Robo.ai, Unmuted", "Bruno Reis, Unmuted"], 30)
        self.assertEqual(tr.speakers(), ["Bruno Reis"])

    def test_bloco_mudo_e_painel_aberto_vale_o_aberto(self):
        tr = RosterTracker()
        self.feed(tr, [tile("Ana Lima", "muted"), "Ana Lima, Unmuted"], 30)
        self.assertEqual(tr.speakers(), ["Ana Lima"])

    def test_suspensao_nao_vira_tempo_de_mic_aberto(self):
        tr = RosterTracker()
        tr.feed([tile("Ana Lima")], 0.0)
        tr.feed([tile("Ana Lima")], 3600.0)  # uma hora sem amostra (máquina dormiu)
        self.assertLess(tr._open_s["Ana Lima"], teamsroster.MIN_UNMUTED_SECONDS)

    def test_teams_em_outro_idioma_nao_gera_amostra(self):
        """Nada reconhecido não pode virar "ninguém falou" - seria sugerir 0."""
        tr = RosterTracker()
        self.feed(tr, ["Ana Lima, vídeo ligado, mudo, Menu de contexto disponível"], 60)
        self.assertEqual(tr.samples, 0)

    def test_ordem_de_quem_abriu_primeiro(self):
        tr = RosterTracker()
        t = self.feed(tr, ["Bruno Reis, Unmuted", "Ana Lima, Muted"], 30)
        self.feed(tr, ["Bruno Reis, Unmuted", "Ana Lima, Unmuted"], 30, start=t)
        self.assertEqual(tr.speakers(), ["Bruno Reis", "Ana Lima"])


class RosterWatchTests(unittest.TestCase):
    def test_sem_amostra_nao_ha_resultado(self):
        self.assertIsNone(RosterWatch().stop())

    def test_resultado_com_total(self):
        w = RosterWatch()
        t = 0.0
        while t <= 30:
            w.tracker.feed(["In this meeting, 9 total", "Ana Lima, Unmuted", "Bruno Reis, Muted"], t)
            t += 5
        self.assertEqual(w.stop(), {"speakers": ["Ana Lima"], "total": 9})

    def test_amostra_le_so_a_janela_da_call(self):
        """O conteúdo da janela principal (conversas) nunca é lido: dela, só o avatar."""
        lidas = []

        class FakeUia:
            @staticmethod
            def names_under(hwnd, automation_id, stop=None):
                lidas.append(("perfil", hwnd, automation_id))
                return ["Profile picture of Ana Lima."]

            @staticmethod
            def descendant_names(hwnd, control_types, stop=None):
                lidas.append(("nomes", hwnd))
                return ["Bruno Reis, Unmuted"]

        class FakeTitles:
            @staticmethod
            def windows(exes):
                return [(1, "Chat | Fulano | Contoso | conta@contoso.com | Microsoft Teams"),
                        (2, "Daily | Contoso | conta@contoso.com | Microsoft Teams")]

        from scriba.detector import _is_teams_call_window

        w = RosterWatch()
        w._sample(FakeUia, FakeTitles, _is_teams_call_window)
        self.assertEqual(lidas, [("perfil", 1, "idna-me-control-avatar-trigger"), ("nomes", 2)])
        self.assertEqual(w.tracker.self_name, "Ana Lima")
        self.assertEqual(w.tracker.samples, 1)

    def test_janela_lida_sem_nada_reconhecido_aparece_no_log_sem_nomes(self):
        """Formato novo do Teams (ou outro idioma) não pode ser invisível em produção,
        e o log não pode levar nome de ninguém (vai no "Reportar erro")."""
        class FakeUia:
            @staticmethod
            def names_under(*a, **k):
                return []

            @staticmethod
            def descendant_names(hwnd, control_types, stop=None):
                return ["Ana Lima, formato desconhecido", "Bruno Reis"]

        class FakeTitles:
            @staticmethod
            def windows(exes):
                return [(2, "Daily | Contoso | conta@contoso.com | Microsoft Teams")]

        from scriba.detector import _is_teams_call_window

        w = RosterWatch()
        w._sample(FakeUia, FakeTitles, _is_teams_call_window)
        with self.assertLogs("scriba.teamsroster", "INFO") as cm:
            self.assertIsNone(w.stop())
        joined = "\n".join(cm.output)
        self.assertIn("1 leitura(s) da janela da call (até 2 textos)", joined)
        self.assertNotIn("Ana", joined)
        self.assertNotIn("Bruno", joined)

    def test_sem_janela_de_call_nao_le_nada(self):
        class FakeUia:
            @staticmethod
            def names_under(*a, **k):
                raise AssertionError("leu a janela principal sem call")

            descendant_names = names_under

        class FakeTitles:
            @staticmethod
            def windows(exes):
                return [(1, "Contoso | conta@contoso.com | Microsoft Teams")]

        from scriba.detector import _is_teams_call_window

        RosterWatch()._sample(FakeUia, FakeTitles, _is_teams_call_window)


class PopupTextTests(unittest.TestCase):
    def test_texto_da_origem(self):
        from scriba.qt.speakers_ui import teams_speakers_text

        self.assertEqual(teams_speakers_text(None), "")
        self.assertIn("ninguém além de você", teams_speakers_text([]))
        self.assertEqual(teams_speakers_text(["Ana Lima"]),
                         "No Teams, 1 pessoa abriu o microfone nesta call: Ana Lima.")
        muitos = teams_speakers_text([f"P{i}" for i in range(9)], max_names=6)
        self.assertTrue(muitos.startswith("No Teams, 9 pessoas abriram"))
        self.assertIn("P5 e mais 3.", muitos)


if __name__ == "__main__":
    unittest.main()
