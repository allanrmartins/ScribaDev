"""Login do claude CLI: checagem diária, marcador vindo do subprocesso e toast.

Com a CLI deslogada, toda reunião sai sem resumo e o usuário só descobria ao
abrir a nota. O app passa a checar o login e avisar (banner + toast)."""

import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import claude_auth, util  # noqa: E402
from scriba.config import Summary  # noqa: E402


class _Isolado(unittest.TestCase):
    def setUp(self):
        self._state0 = util.STATE_PATH
        self._tmpd = tempfile.TemporaryDirectory()
        util.STATE_PATH = Path(self._tmpd.name) / "state.json"

    def tearDown(self):
        util.STATE_PATH = self._state0
        self._tmpd.cleanup()


def _cfg(enabled=True, provider="claude"):
    return type("C", (), {"summary": Summary(enabled=enabled, provider=provider)})()


class AppliesTests(unittest.TestCase):
    def test_so_resumo_ligado_no_claude(self):
        self.assertTrue(claude_auth.applies(_cfg()))
        self.assertTrue(claude_auth.applies(_cfg(provider=" Claude ")))
        self.assertFalse(claude_auth.applies(_cfg(enabled=False)))
        self.assertFalse(claude_auth.applies(_cfg(provider="ollama")))
        self.assertFalse(claude_auth.applies(_cfg(provider="openai")))
        self.assertFalse(claude_auth.applies(object()))


class StatusTests(unittest.TestCase):
    def _run(self, stdout, rc=0):
        p = type("P", (), {"returncode": rc, "stdout": stdout, "stderr": ""})()
        return mock.patch("scriba.claude_auth.subprocess.run", return_value=p)

    def setUp(self):
        self._cmd = mock.patch("scriba.util.claude_command", return_value=["claude"])
        self._cmd.start()

    def tearDown(self):
        self._cmd.stop()

    def test_logado(self):
        with self._run('{"loggedIn": true, "authMethod": "claude.ai"}'):
            self.assertIs(claude_auth.status(), True)

    def test_deslogado_sai_1_mas_decide_pelo_json(self):
        with self._run('{\n  "loggedIn": false,\n  "authMethod": "none"\n}\n', rc=1):
            self.assertIs(claude_auth.status(), False)

    def test_saida_ilegivel_e_desconhecido(self):
        with self._run("Unknown command: auth", rc=1):
            self.assertIsNone(claude_auth.status())
        with self._run('{"authMethod": "none"}'):
            self.assertIsNone(claude_auth.status())

    def test_cli_ausente_nao_e_deslogado(self):
        with mock.patch("scriba.util.claude_command", return_value=None):
            self.assertIsNone(claude_auth.status())

    def test_cli_travada_e_desconhecido(self):
        with mock.patch("scriba.claude_auth.subprocess.run",
                        side_effect=subprocess.TimeoutExpired("claude", 20)):
            self.assertIsNone(claude_auth.status())


class CheckTests(_Isolado):
    def test_gate_diario(self):
        with mock.patch.object(claude_auth, "status", return_value=True) as st:
            self.assertIs(claude_auth.check(), True)
            self.assertIsNone(claude_auth.check())       # já checou hoje
            self.assertIs(claude_auth.check(force=True), True)
        self.assertEqual(st.call_count, 2)

    def test_inconclusivo_nao_fecha_o_dia(self):
        with mock.patch.object(claude_auth, "status", return_value=None):
            self.assertIsNone(claude_auth.check())
        with mock.patch.object(claude_auth, "status", return_value=False):
            self.assertIs(claude_auth.check(), False)

    def test_marcador_tira_a_prova_com_chamada_real(self):
        # sessão OAuth vencida: o status diz logado, mas o resumo bateu em logout
        claude_auth.mark_logged_out()
        with mock.patch.object(claude_auth, "status", return_value=True), \
                mock.patch.object(claude_auth, "probe", return_value=False) as pr:
            self.assertIs(claude_auth.check(), False)
        pr.assert_called_once()
        self.assertTrue(claude_auth.marked())

    def test_prova_ok_limpa_o_marcador(self):
        claude_auth.mark_logged_out()
        with mock.patch.object(claude_auth, "status", return_value=True), \
                mock.patch.object(claude_auth, "probe", return_value=True):
            self.assertIs(claude_auth.check(), True)
        self.assertFalse(claude_auth.marked())

    def test_sem_marcador_nao_gasta_chamada_real(self):
        with mock.patch.object(claude_auth, "status", return_value=True), \
                mock.patch.object(claude_auth, "probe") as pr:
            claude_auth.check(force=True)
        pr.assert_not_called()

    def test_manual_usa_chamada_real(self):
        with mock.patch.object(claude_auth, "status", return_value=True), \
                mock.patch.object(claude_auth, "probe", return_value=True) as pr:
            self.assertIs(claude_auth.check(real=True), True)
        pr.assert_called_once()

    def test_status_deslogado_dispensa_a_prova(self):
        claude_auth.mark_logged_out()
        with mock.patch.object(claude_auth, "status", return_value=False), \
                mock.patch.object(claude_auth, "probe") as pr:
            self.assertIs(claude_auth.check(), False)
        pr.assert_not_called()


class ToastTests(_Isolado):
    def test_repete_a_cada_intervalo(self):
        self.assertTrue(claude_auth.should_toast(now=1000.0))
        claude_auth.toasted(now=1000.0)
        self.assertFalse(claude_auth.should_toast(now=1000.0 + claude_auth.TOAST_EVERY_S - 1))
        self.assertTrue(claude_auth.should_toast(now=1000.0 + claude_auth.TOAST_EVERY_S))
        claude_auth.reset_toast()
        self.assertTrue(claude_auth.should_toast(now=1001.0))


class AppBannerToastTests(_Isolado):
    """ScribaApp._on_claude_auth: estado do banner + toast com repetição."""

    def _app(self):
        from scriba.main import ScribaApp

        app = ScribaApp.__new__(ScribaApp)
        app.claude_logged_out = False
        self.toasts = []
        app._toast = lambda title, body="": self.toasts.append(title)
        return app

    def test_deslogado_acende_banner_e_avisa_uma_vez_no_intervalo(self):
        app = self._app()
        app._on_claude_auth(False)
        self.assertTrue(app.claude_logged_out)
        self.assertEqual(self.toasts, ["Claude CLI deslogado"])
        app._on_claude_auth(False)          # próximo tick, antes das 2 h
        self.assertEqual(len(self.toasts), 1)

    def test_login_apaga_banner_e_rearma_o_toast(self):
        app = self._app()
        app._on_claude_auth(False)
        app._on_claude_auth(True)
        self.assertFalse(app.claude_logged_out)
        app._on_claude_auth(False)          # deslogou de novo: avisa na hora
        self.assertEqual(len(self.toasts), 2)

    def test_resumo_desligado_nao_checa_e_apaga_o_banner(self):
        app = self._app()
        app.claude_logged_out = True
        app.cfg = _cfg(enabled=False)
        done = threading.Event()
        app.ui = lambda fn: (fn(), done.set())
        with mock.patch.object(claude_auth, "check") as ck:
            app._claude_auth_check(force=True)
            self.assertTrue(done.wait(5))
        ck.assert_not_called()
        self.assertFalse(app.claude_logged_out)

    def test_deslogado_rechecagem_ignora_o_gate_diario(self):
        app = self._app()
        app.claude_logged_out = True
        app.cfg = _cfg()
        done = threading.Event()
        app.ui = lambda fn: (fn(), done.set())
        with mock.patch.object(claude_auth, "check", return_value=True) as ck:
            app._claude_auth_check()
            self.assertTrue(done.wait(5))
        self.assertTrue(ck.call_args.kwargs["force"])
        self.assertFalse(app.claude_logged_out)


if __name__ == "__main__":
    unittest.main()
