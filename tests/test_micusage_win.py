"""Detecção por sessões de captura do WASAPI (Windows 11 25H2 parou de atualizar o
ConsentStore do registro) e a síntese de carimbos comum ao macOS (micstamp)."""

import sys
import unittest
from unittest import mock

from scriba import detector
from scriba.micstamp import MicStamps


class TestMicStamps(unittest.TestCase):
    def test_abre_fecha_reabre(self):
        st = MicStamps()
        s1 = {k: (a, b) for k, a, b in st.update({"teams": True})}
        start1, stop1 = s1["teams"]
        self.assertGreater(start1, 0)
        self.assertEqual(stop1, 0)  # mic aberto = análogo do LastUsedTimeStop == 0

        s2 = {k: (a, b) for k, a, b in st.update({"teams": False})}
        self.assertEqual(s2["teams"][0], start1)  # fechar não mexe no start
        self.assertGreaterEqual(s2["teams"][1], start1)

        s3 = {k: (a, b) for k, a, b in st.update({"teams": True})}
        self.assertGreaterEqual(s3["teams"][0], s2["teams"][1])  # REABRIR reescreve o start (#34)
        self.assertEqual(s3["teams"][1], 0)

    def test_poll_repetido_nao_mexe_no_start(self):
        # o detector pode ler a fonte mais de uma vez por poll: sem transição, nada muda
        st = MicStamps()
        primeiro = st.update({"teams": True})
        self.assertEqual(st.update({"teams": True}), primeiro)

    def test_processo_sumiu_fecha_sessao(self):
        st = MicStamps()
        st.update({"zoom": True})
        s = {k: (a, b) for k, a, b in st.update({})}
        self.assertNotEqual(s["zoom"][1], 0)


@unittest.skipUnless(sys.platform == "win32", "micusage_win carrega ole32/kernel32")
class TestMicusageWin(unittest.TestCase):
    TEAMS = r"C:\Program Files\WindowsApps\MSTeams_x64\ms-teams.exe"
    CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

    def setUp(self):
        from scriba import micusage_win

        self.mu = micusage_win
        self.mu.reset()
        self.addCleanup(self.mu.reset)

    def _snap(self, sessions, paths):
        with mock.patch.object(self.mu, "_capture_sessions", return_value=sessions), \
                mock.patch.object(self.mu, "_image_path", side_effect=lambda pid: paths.get(pid, "")):
            return {k: (a, b) for k, a, b in self.mu.snapshot()}

    def test_chave_no_formato_nonpackaged(self):
        s = self._snap([(10, True)], {10: self.TEAMS})
        (key,) = s
        # o pattern "teams" (substring) e o basename do navegador valem como no registro
        self.assertIn("teams", key.lower())
        self.assertEqual(detector._key_basename(key), "ms-teams.exe")

    def test_processos_do_mesmo_exe_fundem(self):
        # Chrome: várias sessões de vários processos; basta uma ativa
        s = self._snap([(20, False), (21, True), (22, False)],
                       {20: self.CHROME, 21: self.CHROME, 22: self.CHROME})
        self.assertEqual(len(s), 1)
        self.assertEqual(next(iter(s.values()))[1], 0)

    def test_sessao_nunca_ativa_nao_entra_no_historico(self):
        # o Teams tem sessão de captura inativa desde a última call: não é call
        self.assertEqual(self._snap([(10, False)], {10: self.TEAMS}), {})

    def test_ativa_depois_inativa_fecha(self):
        self._snap([(10, True)], {10: self.TEAMS})
        s = self._snap([(10, False)], {10: self.TEAMS})
        self.assertNotEqual(next(iter(s.values()))[1], 0)

    def test_pid_zero_e_processo_encerrado_ignorados(self):
        self.assertEqual(self._snap([(0, True), (99, True)], {}), {})


@unittest.skipUnless(sys.platform == "win32", "caminho do registro/WASAPI é do Windows")
class TestIterMicKeysWindows(unittest.TestCase):
    def setUp(self):
        detector._wasapi_failed_logged = False
        self.addCleanup(setattr, detector, "_wasapi_failed_logged", False)

    def test_wasapi_e_a_fonte_principal(self):
        from scriba import micusage_win

        with mock.patch.object(micusage_win, "snapshot", return_value=[("C:#x#ms-teams.exe", 5, 0)]), \
                mock.patch.object(detector, "_iter_registry_keys", side_effect=AssertionError("registro lido")):
            self.assertEqual(list(detector._iter_mic_keys()), [("C:#x#ms-teams.exe", 5, 0)])

    def test_com_falhou_cai_no_registro(self):
        from scriba import micusage_win

        with mock.patch.object(micusage_win, "snapshot", side_effect=OSError("COM")), \
                mock.patch.object(detector, "_iter_registry_keys", return_value=iter([("MSTeams_x", 1, 0)])), \
                self.assertLogs("scriba.detector", "ERROR"):
            self.assertEqual(list(detector._iter_mic_keys()), [("MSTeams_x", 1, 0)])

    def test_doctor_une_registro_e_sessoes(self):
        from scriba import micusage_win

        with mock.patch.object(detector, "_iter_registry_keys", return_value=iter([("MSTeams_x", 1, 2)])), \
                mock.patch.object(micusage_win, "sessions_by_key",
                                  return_value={r"C:#Chrome#Application#chrome.exe": False}):
            self.assertEqual(detector.app_key_status(["teams", "zoom"]), {"Teams": True, "Zoom": False})
        with mock.patch.object(detector, "_iter_registry_keys", return_value=iter([])), \
                mock.patch.object(micusage_win, "sessions_by_key",
                                  return_value={r"C:#Chrome#Application#chrome.exe": False}):
            self.assertEqual(detector.browser_key_status(["chrome", "msedge"]),
                             {"chrome": True, "msedge": False})


@unittest.skipUnless(sys.platform == "win32", "enumera o WASAPI de verdade")
class TestCaptureSessionsReal(unittest.TestCase):
    """Fumaça contra o COM real: enumerar não pode levantar (mesmo sem microfone)."""

    def test_enumera_sem_erro_em_outra_thread(self):
        # o detector roda em thread própria: o COM tem de subir nela também
        import threading

        from scriba import micusage_win

        erro = []

        def alvo():
            try:
                micusage_win.sessions_by_key()
            except Exception as e:  # noqa: BLE001 - o teste reporta qualquer falha
                erro.append(e)

        t = threading.Thread(target=alvo)
        t.start()
        t.join(10)
        self.assertEqual(erro, [])


if __name__ == "__main__":
    unittest.main()
