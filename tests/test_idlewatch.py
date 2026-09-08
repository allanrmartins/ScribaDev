"""Sentinela de travamento do subprocesso (#188): faulthandler despeja as pilhas em
hang.log na pasta da reunião quando o processo fica sem batimento; batimento
(stdout/estágio/bloco) rearma; o resumo suspende; sem dump o arquivo some."""

import io
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scriba import idlewatch  # noqa: E402


class IdleWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_iw_"))
        self.folder = self.tmp / "2026" / "09" / "08" / "15-05_Reuniao Vetra"
        self.folder.mkdir(parents=True)
        self._stdout = sys.stdout

    def tearDown(self):
        idlewatch.disarm()
        sys.stdout = self._stdout
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wait_dump(self, path: Path, timeout: float = 5.0) -> str:
        fim = time.monotonic() + timeout
        while time.monotonic() < fim:
            try:
                txt = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                txt = ""
            if "Thread" in txt or "Timeout" in txt:
                return txt
            time.sleep(0.05)
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def test_sem_batimento_despeja_pilhas_no_hang_log_da_pasta(self):
        self.assertTrue(idlewatch.arm(self.folder, idle_s=0.3))
        path = self.folder / "hang.log"
        self.assertTrue(path.exists())
        txt = self._wait_dump(path)
        self.assertIn("sentinela armada", txt)
        self.assertIn("Thread", txt)            # o faulthandler despejou as pilhas
        self.assertIn("test_idlewatch", txt)    # inclusive a desta thread (todas as threads)
        self.assertTrue(idlewatch.dumped())
        idlewatch.disarm(note="processo seguiu")
        txt = path.read_text(encoding="utf-8", errors="replace")
        self.assertIn("processo seguiu", txt)   # com dump, o arquivo FICA e ganha a nota
        self.assertFalse((self.folder / "process.log").exists())  # nunca escreve no process.log

    def test_batimentos_seguram_o_dump_e_sem_dump_o_arquivo_some(self):
        with mock.patch.object(idlewatch, "_MIN_REARM_S", 0.0):   # a batida rearma já
            idlewatch.arm(self.folder, idle_s=0.5)
            fim = time.monotonic() + 1.5
            while time.monotonic() < fim:
                idlewatch.beat()
                time.sleep(0.1)
            self.assertFalse(idlewatch.dumped())
            idlewatch.disarm()
        self.assertFalse((self.folder / "hang.log").exists())

    def test_escrita_no_stdout_e_batimento(self):
        with mock.patch.object(idlewatch, "_MIN_REARM_S", 0.0):
            sys.stdout = io.StringIO()
            idlewatch.arm(self.folder, idle_s=0.5)
            fim = time.monotonic() + 1.5
            while time.monotonic() < fim:
                print("34 segmentos")   # linha de progresso do process.log
                time.sleep(0.1)
            self.assertFalse(idlewatch.dumped())
            self.assertIn("34 segmentos", sys.stdout.getvalue())  # e a saída passa intacta
            idlewatch.disarm()
            self.assertIsInstance(sys.stdout, io.StringIO)   # stdout restaurado no desarme

    def test_suspender_durante_o_resumo_nao_despeja(self):
        idlewatch.arm(self.folder, idle_s=0.3)
        idlewatch.suspend()
        time.sleep(0.8)
        self.assertFalse(idlewatch.dumped())
        idlewatch.resume()
        txt = self._wait_dump(self.folder / "hang.log")
        self.assertIn("Thread", txt)   # depois do resume volta a vigiar

    def test_armar_de_novo_na_mesma_pasta_e_idempotente(self):
        idlewatch.arm(self.folder, idle_s=5)
        self.assertTrue(idlewatch.arm(self.folder, idle_s=5))
        self.assertIs(sys.stdout.__class__, idlewatch._BeatingStream)  # embrulhado UMA vez
        self.assertIsNot(sys.stdout._s.__class__, idlewatch._BeatingStream)
        idlewatch.disarm()

    def test_pasta_inexistente_nao_derruba(self):
        self.assertFalse(idlewatch.arm(self.tmp / "nao-existe" / "x", idle_s=5))
        idlewatch.beat()
        idlewatch.disarm()   # sem estado: no-op


if __name__ == "__main__":
    unittest.main()
