"""Watchdog da GUI (freeze de 2026-07-08): dump 1x por episódio + retomada logada."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import util  # noqa: E402
from scriba.watchdog import GuiWatchdog  # noqa: E402


class GuiWatchdogTests(unittest.TestCase):
    def _wd(self, threshold=10.0):
        dumps: list[float] = []
        wd = GuiWatchdog(threshold_s=threshold, dump=dumps.append)
        return wd, dumps

    def test_nao_dispara_com_heartbeat_vivo(self):
        wd, dumps = self._wd()
        wd.beat(now=100.0)
        self.assertFalse(wd.check(now=105.0))    # 5s < 10s
        wd.beat(now=105.0)
        self.assertFalse(wd.check(now=114.0))    # bateu de novo: janela reiniciou
        self.assertEqual(dumps, [])

    def test_dispara_uma_vez_por_episodio(self):
        wd, dumps = self._wd()
        wd.beat(now=100.0)
        self.assertTrue(wd.check(now=111.0))     # 11s travada -> dump
        self.assertFalse(wd.check(now=120.0))    # MESMO episódio: não despeja de novo
        self.assertFalse(wd.check(now=300.0))
        self.assertEqual(len(dumps), 1)
        self.assertAlmostEqual(dumps[0], 11.0)

    def test_rearma_apos_retomada(self):
        wd, dumps = self._wd()
        wd.beat(now=100.0)
        wd.check(now=111.0)                      # episódio 1
        wd.beat(now=112.0)                       # GUI voltou
        with self.assertLogs("scriba.watchdog", level="WARNING") as cm:
            self.assertFalse(wd.check(now=113.0))          # retomada: loga a duração
        self.assertIn("voltou a responder", cm.output[0])
        self.assertTrue(wd.check(now=130.0))     # episódio 2: despeja de novo
        self.assertEqual(len(dumps), 2)

    def test_marcador_precoce_antes_do_dump(self):
        """#161: travamento curto finalizado pelo usuário no "não está respondendo"
        morria sem NENHUM rastro (o dump só vem no limiar cheio) — a partir de
        `early_s` fica UMA linha de warning no scriba.log, sem despejar pilhas."""
        wd, dumps = self._wd()
        wd.beat(now=100.0)
        with self.assertLogs("scriba.watchdog", level="WARNING") as cm:
            self.assertFalse(wd.check(now=107.0))    # 7s: marca, não despeja
        self.assertIn("marcador precoce", cm.output[0])
        self.assertEqual(dumps, [])
        with self.assertNoLogs("scriba.watchdog", level="WARNING"):
            self.assertFalse(wd.check(now=109.0))    # mesmo episódio: não repete
        self.assertTrue(wd.check(now=111.0))         # limiar cheio: dump normal
        # retomada rearma o marcador p/ o próximo episódio
        wd.beat(now=112.0)
        wd.check(now=112.5)
        with self.assertLogs("scriba.watchdog", level="WARNING") as cm2:
            self.assertFalse(wd.check(now=118.5))    # novo episódio, 6s
        self.assertIn("marcador precoce", cm2.output[-1])
        self.assertEqual(len(dumps), 1)

    def test_dump_de_falha_nao_derruba_o_monitor(self):
        def boom(_s):
            raise RuntimeError("x")
        wd = GuiWatchdog(threshold_s=10.0, dump=boom)
        wd.beat(now=100.0)
        self.assertTrue(wd.check(now=111.0))     # engole a exceção (só loga)

    def test_dump_real_escreve_pilhas_no_hang_log(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_wd_"))
        logs0 = util.LOGS_DIR
        util.LOGS_DIR = d
        try:
            wd = GuiWatchdog(threshold_s=10.0)   # dump default (faulthandler)
            wd.beat(now=100.0)
            self.assertTrue(wd.check(now=115.0))
            text = (d / "hang.log").read_text(encoding="utf-8", errors="replace")
            self.assertIn("GUI sem responder", text)
            self.assertIn("Current thread", text)   # faulthandler listou as threads
            self.assertIn("test_watchdog", text)  # inclui ESTA pilha (arquivo do teste)
        finally:
            util.LOGS_DIR = logs0


if __name__ == "__main__":
    unittest.main(verbosity=2)
