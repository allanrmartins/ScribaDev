"""Testes de scriba.detector: máquina de estados do Detector e _clean_meeting_title."""

import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import detector  # noqa: E402
from scriba.config import Detection  # noqa: E402
from scriba.detector import CallState, Detector, _clean_meeting_title  # noqa: E402


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


class _Clock:
    """Relógio monotônico controlado (substitui time.monotonic nos testes)."""

    def __init__(self, start: float = 1000.0):
        self.t = start

    def monotonic(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _StopAfter:
    """stop_event falso: deixa Detector.run fazer exatamente n iterações do loop."""

    def __init__(self, n: int):
        self.n = n
        self.waits = 0

    def is_set(self) -> bool:
        return self.waits >= self.n

    def wait(self, _timeout: float) -> None:
        self.waits += 1


class DetectorStateMachineTests(unittest.TestCase):
    """Máquina de estados + observabilidade (#36). O registro do Windows é
    substituído por um fake de _sense_call e o tempo por um _Clock."""

    def setUp(self):
        self.clock = _Clock()
        p = mock.patch.object(detector.time, "monotonic", self.clock.monotonic)
        p.start()
        self.addCleanup(p.stop)
        self.events: list[str] = []
        self.mic: str | None = None  # app com o mic aberto agora, ou None

    def _make(self, **over) -> Detector:
        params = dict(grace_seconds=8.0, poll_seconds=2.0, max_call_hours=4.0)
        params.update(over)
        det = Detector(
            Detection(**params),
            on_call_started=lambda: self.events.append("started"),
            on_call_ended=lambda: self.events.append("ended"),
            on_grace=lambda: self.events.append("grace"),
            on_grace_cancel=lambda: self.events.append("resume"),
        )
        det._sense_call = lambda: self.mic  # controla quem está em call
        self.det = det
        return det

    def _poll(self, mic: str | None, advance: float = 0.0) -> None:
        if advance:
            self.clock.advance(advance)
        self.mic = mic
        self.det.poll_once()

    # -------- transições básicas --------

    def test_idle_para_recording(self):
        self._make()
        self._poll("Teams")
        self.assertIs(self.det.state, CallState.RECORDING)
        self.assertEqual(self.events, ["started"])

    def test_grace_e_encerramento_por_timeout(self):
        self._make()
        self._poll("Teams")
        with self.assertLogs("scriba.detector", "INFO") as cm:
            self._poll(None)  # mic liberado -> GRACE
        self.assertIs(self.det.state, CallState.GRACE)
        self.assertIn("mic liberado (Teams) - tolerância de 8s", "\n".join(cm.output))
        with self.assertLogs("scriba.detector", "INFO") as cm:
            self._poll(None, advance=9.0)  # passou o grace inteiro
        self.assertIs(self.det.state, CallState.IDLE)
        self.assertIn("call encerrada (mic livre > 8s)", "\n".join(cm.output))
        self.assertEqual(self.events, ["started", "grace", "ended"])

    # -------- resume e o gap medido (#36) --------

    def test_resume_dentro_do_grace_loga_gap(self):
        self._make()
        self._poll("Teams")
        self._poll(None)  # -> GRACE
        with self.assertLogs("scriba.detector", "INFO") as cm:
            self._poll("Teams", advance=4.0)  # mic volta em 4s (mesma call)
        self.assertIs(self.det.state, CallState.RECORDING)
        self.assertIn("mic voltou após 4.0s - mesma call", "\n".join(cm.output))
        self.assertIn("resume", self.events)

    def test_resume_em_app_diferente_loga_warning(self):
        # Teams libera o mic e o Zoom pega dentro do grace: hoje emenda na mesma
        # gravação (a fusão do incidente 2026-07-02); o log tem de deixar isso VISÍVEL.
        self._make()
        self._poll("Teams")
        self._poll(None)  # -> GRACE
        with self.assertLogs("scriba.detector", "WARNING") as cm:
            self._poll("Zoom", advance=3.0)
        self.assertIs(self.det.state, CallState.RECORDING)
        out = "\n".join(cm.output)
        self.assertIn("app diferente", out)
        self.assertIn("Teams -> Zoom", out)

    # -------- troca de app sem grace (Teams -> Zoom direto) --------

    def test_troca_de_app_durante_recording_loga_warning(self):
        self._make()
        self._poll("Teams")  # RECORDING
        self._poll("Teams")  # segue igual, sem aviso
        with self.assertLogs("scriba.detector", "WARNING") as cm:
            self._poll("Zoom")  # troca sem passar pelo grace
        self.assertIs(self.det.state, CallState.RECORDING)
        self.assertIn(
            "troca de app monitorado durante a call: Teams -> Zoom", "\n".join(cm.output)
        )

    # -------- parada de segurança por max_call_hours --------

    def test_max_call_hours_encerra_e_loga(self):
        self._make(max_call_hours=1.0)
        self._poll("Teams")
        with self.assertLogs("scriba.detector", "INFO") as cm:
            self._poll("Teams", advance=3601.0)  # > 1h com o mic ainda aberto
        self.assertIs(self.det.state, CallState.IDLE)
        self.assertIn("limite de segurança de 1h", "\n".join(cm.output))
        self.assertEqual(self.events, ["started", "ended"])

    # -------- rate-limit do log de exceções do loop --------

    def test_excecao_no_poll_logada_uma_vez_por_minuto(self):
        det = self._make()
        det._sense_call = mock.Mock(side_effect=RuntimeError("registro falhou"))
        with self.assertLogs("scriba.detector", "ERROR") as cm:
            det.run(_StopAfter(2))  # 2 polls no MESMO instante
        falhas = [line for line in cm.output if "falha no poll" in line]
        self.assertEqual(len(falhas), 1)  # o segundo poll (< 60s) é suprimido

    def test_excecao_no_poll_volta_a_logar_apos_60s(self):
        det = self._make()
        det._sense_call = mock.Mock(side_effect=RuntimeError("registro falhou"))
        with self.assertLogs("scriba.detector", "ERROR") as cm:
            det.run(_StopAfter(1))
        self.assertEqual(len(cm.output), 1)
        self.clock.advance(61.0)
        with self.assertLogs("scriba.detector", "ERROR") as cm:
            det.run(_StopAfter(1))  # já passou 1 min -> loga de novo
        self.assertEqual(len(cm.output), 1)


if __name__ == "__main__":
    unittest.main()
