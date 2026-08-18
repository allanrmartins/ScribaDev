"""Sonda de responsividade do shell (#161): fail-open por princípio — a sonda evita
congelar a GUI (Shell_NotifyIcon com Explorer pendurado), nunca prende a bandeja."""

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import shellprobe  # noqa: E402


class ShellProbeTests(unittest.TestCase):
    def setUp(self):
        self._saved = (shellprobe._SUPPORTED, shellprobe._started,
                       shellprobe._ok, shellprobe._checked)

    def tearDown(self):
        (shellprobe._SUPPORTED, shellprobe._started,
         shellprobe._ok, shellprobe._checked) = self._saved

    def test_fail_open_fora_do_windows(self):
        shellprobe._SUPPORTED = False
        shellprobe._started = True
        shellprobe._ok = False
        self.assertTrue(shellprobe.responsive())

    def test_fail_open_sem_sonda_no_ar(self):
        shellprobe._SUPPORTED = True
        shellprobe._started = False
        shellprobe._ok = False
        self.assertTrue(shellprobe.responsive())

    def test_dado_fresco_manda(self):
        shellprobe._SUPPORTED = True
        shellprobe._started = True
        shellprobe._checked = time.monotonic()
        shellprobe._ok = False
        self.assertFalse(shellprobe.responsive())   # shell pendurado → pula cosmético
        shellprobe._ok = True
        self.assertTrue(shellprobe.responsive())

    def test_dado_velho_e_otimista(self):
        # sonda sem dados frescos (thread morta/processo suspenso) não pode prender
        shellprobe._SUPPORTED = True
        shellprobe._started = True
        shellprobe._ok = False
        shellprobe._checked = time.monotonic() - 99.0
        self.assertTrue(shellprobe.responsive())

    def test_probe_sem_janela_de_bandeja_e_true(self):
        # sessão sem Explorer (CI): não há quem pendurar — e não pode travar o pulso
        with mock.patch.object(shellprobe, "_find_tray_window", return_value=0), \
                mock.patch.object(shellprobe, "_send_null_with_timeout") as send:
            self.assertTrue(shellprobe._probe_once())
        send.assert_not_called()

    def test_probe_reflete_o_send_timeout(self):
        with mock.patch.object(shellprobe, "_find_tray_window", return_value=42), \
                mock.patch.object(shellprobe, "_send_null_with_timeout", return_value=False):
            self.assertFalse(shellprobe._probe_once())   # timeout = pendurado
        with mock.patch.object(shellprobe, "_find_tray_window", return_value=42), \
                mock.patch.object(shellprobe, "_send_null_with_timeout", return_value=True):
            self.assertTrue(shellprobe._probe_once())


if __name__ == "__main__":
    unittest.main(verbosity=2)
