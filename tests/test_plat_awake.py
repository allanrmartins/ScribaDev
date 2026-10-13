"""plat.awake_seconds: relógio dos vigias de travamento que não conta a suspensão.

O time.monotonic do Windows segue contando com a máquina dormindo: o watchdog da
GUI acusava "~15 h sem responder" toda manhã (2026-10-12 e 13), e o vigia do
processamento poderia encerrar um filho que só tinha dormido junto."""

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import plat  # noqa: E402


class AwakeSecondsTests(unittest.TestCase):
    def test_anda_como_o_relogio_com_a_maquina_acordada(self):
        a0, m0 = plat.awake_seconds(), time.monotonic()
        time.sleep(0.3)
        da, dm = plat.awake_seconds() - a0, time.monotonic() - m0
        self.assertGreater(da, 0.2)
        # a resolução do QueryUnbiasedInterruptTime é o tick do timer (~16 ms)
        self.assertAlmostEqual(da, dm, delta=0.1)

    def test_monotono(self):
        vals = [plat.awake_seconds() for _ in range(200)]
        self.assertEqual(vals, sorted(vals))

    @unittest.skipUnless(sys.platform == "win32", "QueryUnbiasedInterruptTime é do Windows")
    def test_windows_nao_conta_o_tempo_dormindo(self):
        # monotonic - awake = tempo suspenso desde o boot: nunca negativo
        self.assertLessEqual(plat.awake_seconds(), time.monotonic() + 0.1)

    @unittest.skipUnless(sys.platform == "win32", "QueryUnbiasedInterruptTime é do Windows")
    def test_windows_sem_a_api_cai_no_monotonic(self):
        from scriba.plat import _win

        class _K32:
            def __getattr__(self, name):
                raise AttributeError(name)

        with mock.patch.object(_win.ctypes, "WinDLL", lambda _n: _K32()), \
                self.assertLogs("scriba.plat", level="WARNING"):
            clock = _win._make_awake_clock()
        self.assertIs(clock, time.monotonic)


if __name__ == "__main__":
    unittest.main()
