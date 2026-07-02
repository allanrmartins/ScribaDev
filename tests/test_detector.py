"""Testes de scriba.detector: máquina de estados do Detector e _clean_meeting_title."""

import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import detector  # noqa: E402
from scriba.config import Detection  # noqa: E402
from scriba.detector import CallState, Detector, _clean_meeting_title  # noqa: E402

# FILETIME é contado em intervalos de 100 ns desde 1601.
_FT = 10_000_000                     # 1 segundo
_BASE = 134_000_000_000_000_000      # instante-base arbitrário (~2025)
TEAMS = "MSTeams_8wekyb3d8bbwe"       # casa o pattern "teams"
ZOOM = "Zoom.exe"                     # casa o pattern "zoom"


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
    """Máquina de estados + observabilidade (#36) + divisão de calls (#34).

    O registro do Windows é substituído por um fake de _mic_sessions (dict de
    subchave -> (LastUsedTimeStart, LastUsedTimeStop)) e o tempo por um _Clock.
    """

    def setUp(self):
        self.clock = _Clock()
        p = mock.patch.object(detector.time, "monotonic", self.clock.monotonic)
        p.start()
        self.addCleanup(p.stop)
        self.events: list[tuple] = []
        self.sessions: dict[str, tuple[int, int]] = {}

    def _make(self, **over) -> Detector:
        params = dict(grace_seconds=8.0, poll_seconds=2.0, max_call_hours=4.0, split_gap_seconds=3.0)
        params.update(over)
        det = Detector(
            Detection(**params),
            on_call_started=lambda *a, **k: self.events.append(("started", k.get("probe", True))),
            on_call_ended=lambda: self.events.append(("ended",)),
            on_grace=lambda: self.events.append(("grace",)),
            on_grace_cancel=lambda: self.events.append(("resume",)),
        )
        det._mic_sessions = lambda: dict(self.sessions)  # registro controlado
        self.det = det
        return det

    def _poll(self, sessions: dict[str, tuple[int, int]], advance: float = 0.0) -> None:
        if advance:
            self.clock.advance(advance)
        self.sessions = sessions
        self.det.poll_once()

    def _starts(self) -> list[tuple]:
        return [e for e in self.events if e[0] == "started"]

    # -------- transições básicas --------

    def test_idle_para_recording(self):
        self._make()
        self._poll({TEAMS: (_BASE, 0)})
        self.assertIs(self.det.state, CallState.RECORDING)
        self.assertEqual(self.events, [("started", True)])  # início normal roda a sonda
        self.assertEqual(self.det._session_sub, TEAMS)

    def test_grace_e_encerramento_por_timeout(self):
        self._make()
        self._poll({TEAMS: (_BASE, 0)})
        with self.assertLogs("scriba.detector", "INFO") as cm:
            self._poll({TEAMS: (_BASE, _BASE + 100 * _FT)})  # mic liberado -> GRACE
        self.assertIs(self.det.state, CallState.GRACE)
        self.assertIn("mic liberado (Teams) - tolerância de 8s", "\n".join(cm.output))
        with self.assertLogs("scriba.detector", "INFO") as cm:
            self._poll({TEAMS: (_BASE, _BASE + 100 * _FT)}, advance=9.0)  # passou o grace
        self.assertIs(self.det.state, CallState.IDLE)
        self.assertIn("call encerrada (mic livre > 8s)", "\n".join(cm.output))
        self.assertEqual(self.events, [("started", True), ("grace",), ("ended",)])

    # -------- resume vs. split, mesma subchave (o incidente 2026-07-02) --------

    def test_resume_abaixo_do_limiar_emenda(self):
        self._make()  # split_gap_seconds=3
        self._poll({TEAMS: (_BASE, 0)})
        self._poll({TEAMS: (_BASE, _BASE + 100 * _FT)})  # GRACE, guarda stop
        new_start = _BASE + 100 * _FT + 2 * _FT  # mic reabre 2 s depois (< 3)
        with self.assertLogs("scriba.detector", "INFO") as cm:
            self._poll({TEAMS: (new_start, 0)}, advance=2.0)
        self.assertIs(self.det.state, CallState.RECORDING)
        self.assertIn("mic voltou após 2.0s - mesma call", "\n".join(cm.output))
        self.assertEqual(len(self._starts()), 1)  # não dividiu: uma call só

    def test_split_acima_do_limiar_divide(self):
        # o incidente: Teams (call 1) libera e a MESMA subchave reabre após 5 s (call 2)
        self._make()
        self._poll({TEAMS: (_BASE, 0)})
        self._poll({TEAMS: (_BASE, _BASE + 100 * _FT)})  # GRACE, stop = BASE+100s
        new_start = _BASE + 100 * _FT + 5 * _FT  # gap de 5 s (>= 3)
        with self.assertLogs("scriba.detector", "INFO") as cm:
            self._poll({TEAMS: (new_start, 0)}, advance=5.0)
        out = "\n".join(cm.output)
        self.assertIn("gap de 5.0s >= split_gap_seconds=3", out)
        self.assertIn("dividindo a gravação", out)
        self.assertIs(self.det.state, CallState.RECORDING)  # já gravando a call 2
        self.assertIn(("ended",), self.events)              # parte 1 enfileirada
        self.assertIn(("started", False), self.events)      # parte 2 sem sonda de áudio
        self.assertEqual(self.det._session_start, new_start)

    def test_split_usa_relogio_quando_filetime_desconhecido(self):
        # LastUsedTimeStart == 0 (leitura falhou): cai no gap por relógio de parede
        self._make()
        self._poll({TEAMS: (_BASE, 0)})
        self._poll({TEAMS: (_BASE, _BASE + 100 * _FT)})  # GRACE
        with self.assertLogs("scriba.detector", "INFO") as cm:
            self._poll({TEAMS: (0, 0)}, advance=2.0)  # start desconhecido, 2 s de relógio
        self.assertIs(self.det.state, CallState.RECORDING)
        self.assertIn("mic voltou após 2.0s - mesma call", "\n".join(cm.output))
        self.assertEqual(len(self._starts()), 1)

    # -------- split por troca de app monitorado --------

    def test_split_troca_de_app_no_resume(self):
        # Teams libera o mic e o Zoom o pega dentro do grace -> nova call
        self._make()
        self._poll({TEAMS: (_BASE, 0)})
        self._poll({TEAMS: (_BASE, _BASE + 100 * _FT)})  # GRACE
        with self.assertLogs("scriba.detector", "INFO") as cm:
            self._poll({TEAMS: (_BASE, _BASE + 100 * _FT), ZOOM: (_BASE + 102 * _FT, 0)}, advance=2.0)
        out = "\n".join(cm.output)
        self.assertIn("app diferente (Teams -> Zoom)", out)
        self.assertIn("dividindo a gravação", out)
        self.assertIs(self.det.state, CallState.RECORDING)
        self.assertEqual(self.det._session_sub, ZOOM)
        self.assertIn(("started", False), self.events)

    def test_split_teams_para_zoom_sem_gap(self):
        # Teams fecha e Zoom abre entre o mesmo par de polls (sem passar pelo GRACE)
        self._make()
        self._poll({TEAMS: (_BASE, 0)})
        with self.assertLogs("scriba.detector", "INFO") as cm:
            self._poll({TEAMS: (_BASE, _BASE + 100 * _FT), ZOOM: (_BASE + 100 * _FT, 0)})
        out = "\n".join(cm.output)
        self.assertIn("app trocou", out)
        self.assertIn("dividindo a gravação", out)
        self.assertIs(self.det.state, CallState.RECORDING)
        self.assertEqual(self.det._session_sub, ZOOM)

    # -------- ciclo de sessão invisível: v1 só loga, não divide --------

    def test_ciclo_invisivel_so_loga(self):
        self._make()
        self._poll({TEAMS: (_BASE, 0)})  # RECORDING, session_start = BASE
        with self.assertLogs("scriba.detector", "WARNING") as cm:
            self._poll({TEAMS: (_BASE + 50 * _FT, 0)})  # mesma subchave, start reescrito
        self.assertIs(self.det.state, CallState.RECORDING)  # NÃO dividiu
        self.assertIn("ciclo de sessão invisível", "\n".join(cm.output))
        self.assertEqual(self.det._session_start, _BASE + 50 * _FT)  # rastreamento atualizado
        self.assertEqual(len(self._starts()), 1)

    # -------- split_gap_seconds = 0 desliga (comportamento antigo) --------

    def test_split_desligado_emenda_mesma_sub(self):
        self._make(split_gap_seconds=0.0)
        self._poll({TEAMS: (_BASE, 0)})
        self._poll({TEAMS: (_BASE, _BASE + 100 * _FT)})  # GRACE
        with self.assertLogs("scriba.detector", "INFO") as cm:
            self._poll({TEAMS: (_BASE + 200 * _FT, 0)}, advance=5.0)  # gap 5 s, mas split off
        self.assertIs(self.det.state, CallState.RECORDING)
        self.assertIn("mesma call", "\n".join(cm.output))
        self.assertEqual(len(self._starts()), 1)  # não dividiu

    def test_split_desligado_app_diferente_emenda(self):
        self._make(split_gap_seconds=0.0)
        self._poll({TEAMS: (_BASE, 0)})
        self._poll({TEAMS: (_BASE, _BASE + 100 * _FT)})  # GRACE
        with self.assertLogs("scriba.detector", "WARNING") as cm:
            self._poll({TEAMS: (_BASE, _BASE + 100 * _FT), ZOOM: (_BASE + 102 * _FT, 0)}, advance=2.0)
        out = "\n".join(cm.output)
        self.assertIn("app diferente", out)
        self.assertIn("split desligado", out)
        self.assertIs(self.det.state, CallState.RECORDING)
        self.assertEqual(self.det._session_sub, ZOOM)  # emendou rastreando o Zoom
        self.assertEqual(len(self._starts()), 1)

    # -------- gap > grace: a call já dividia pelo timeout (preservado) --------

    def test_gap_maior_que_grace_gera_duas_calls(self):
        self._make()
        self._poll({TEAMS: (_BASE, 0)})
        self._poll({TEAMS: (_BASE, _BASE + 100 * _FT)})  # GRACE
        self._poll({TEAMS: (_BASE, _BASE + 100 * _FT)}, advance=9.0)  # grace expira -> IDLE
        self.assertIs(self.det.state, CallState.IDLE)
        self._poll({TEAMS: (_BASE + 300 * _FT, 0)}, advance=1.0)  # mic reabre depois -> call nova
        self.assertIs(self.det.state, CallState.RECORDING)
        self.assertEqual(len(self._starts()), 2)

    # -------- parada de segurança por max_call_hours --------

    def test_max_call_hours_encerra_e_loga(self):
        self._make(max_call_hours=1.0)
        self._poll({TEAMS: (_BASE, 0)})
        with self.assertLogs("scriba.detector", "INFO") as cm:
            self._poll({TEAMS: (_BASE, 0)}, advance=3601.0)  # > 1h com o mic aberto
        self.assertIs(self.det.state, CallState.IDLE)
        self.assertIn("limite de segurança de 1h", "\n".join(cm.output))
        self.assertIn(("ended",), self.events)

    # -------- rate-limit do log de exceções do loop --------

    def test_excecao_no_poll_logada_uma_vez_por_minuto(self):
        det = self._make()
        det._mic_sessions = mock.Mock(side_effect=RuntimeError("registro falhou"))
        with self.assertLogs("scriba.detector", "ERROR") as cm:
            det.run(_StopAfter(2))  # 2 polls no MESMO instante
        falhas = [line for line in cm.output if "falha no poll" in line]
        self.assertEqual(len(falhas), 1)  # o segundo poll (< 60s) é suprimido

    def test_excecao_no_poll_volta_a_logar_apos_60s(self):
        det = self._make()
        det._mic_sessions = mock.Mock(side_effect=RuntimeError("registro falhou"))
        with self.assertLogs("scriba.detector", "ERROR") as cm:
            det.run(_StopAfter(1))
        self.assertEqual(len(cm.output), 1)
        self.clock.advance(61.0)
        with self.assertLogs("scriba.detector", "ERROR") as cm:
            det.run(_StopAfter(1))  # já passou 1 min -> loga de novo
        self.assertEqual(len(cm.output), 1)


if __name__ == "__main__":
    unittest.main()
