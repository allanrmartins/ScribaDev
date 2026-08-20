"""Recuperação de reuniões presas (#89).

Bug A: `diarizing` (novo estágio) não estava na tupla de requeue do boot
(scan_pending) nem nos status aceitos por process_when_ready — reunião morta
durante a diarização (o estágio mais pesado) ficava presa para sempre, com a
capa mostrando "Separando vozes…" eternamente.

Bug B: gravação órfã adotada no boot mas mais curta que min_call_seconds
ficava com status "recorded" (não-terminal) — virava linha fantasma "Na fila…"
na capa a cada início do app. Agora ganha "too_short", como no caminho vivo.

Nada pesado roda aqui: ScribaApp via __new__ (sem __init__), repair_folder é
real (pasta sem wavs → 0s) ou mockado, e process_folder é stubado.
"""

import json
import os
import queue
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.main import ScribaApp  # noqa: E402

try:
    import scriba.pipeline as pipeline
    _HAVE_PIPELINE = True
except Exception:  # deps pesadas ausentes
    _HAVE_PIPELINE = False


def _meeting(root: Path, name: str, meta: dict) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return d


class ScanPendingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="scriba_pend_"))

    def _app(self):
        app = ScribaApp.__new__(ScribaApp)
        app.cfg = SimpleNamespace(
            output=SimpleNamespace(resolved_recordings_dir=lambda: self.root),
            detection=SimpleNamespace(min_call_seconds=60),
        )
        app.jobs = queue.Queue()
        app._toast = mock.Mock()
        return app

    def _queued(self, app) -> list[str]:
        out = []
        while not app.jobs.empty():
            out.append(app.jobs.get().name)
        return out

    def test_readota_diarizing_preso(self):
        # regressão #89: morreu durante a diarização → requeue no próximo boot
        _meeting(self.root, "m1", {"status": "diarizing"})
        app = self._app()
        app.scan_pending()
        self.assertEqual(self._queued(app), ["m1"])

    def test_nao_readota_terminais(self):
        for st in ("done", "failed", "no_audio", "too_short", "discarded"):
            _meeting(self.root, f"m_{st}", {"status": st})
        app = self._app()
        app.scan_pending()
        self.assertEqual(self._queued(app), [])

    def test_nao_readota_diarizing_com_lock_ativo(self):
        d = _meeting(self.root, "m1", {"status": "diarizing"})
        (d / ".lock").write_text(
            json.dumps({"pid": os.getpid(), "started": time.time()}), encoding="utf-8"
        )
        app = self._app()
        app.scan_pending()
        self.assertEqual(self._queued(app), [])

    def test_orfa_curta_ganha_too_short_e_nao_enfileira(self):
        # regressão #89: ficava "recorded" eterna → "Na fila…" fantasma na capa
        d = _meeting(self.root, "curta", {"status": "recording"})  # sem wavs → 0s
        app = self._app()
        app.scan_pending()
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "too_short")
        self.assertTrue(meta["interrupted"])
        self.assertEqual(self._queued(app), [])
        # segundo boot: terminal fica terminal (nada de readotar de novo)
        app2 = self._app()
        app2.scan_pending()
        self.assertEqual(self._queued(app2), [])

    def test_orfa_longa_e_adotada_como_recorded(self):
        d = _meeting(self.root, "longa", {"status": "recording"})
        with mock.patch("scriba.recorder.repair_folder", return_value=120.0):
            app = self._app()
            app.scan_pending()
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "recorded")
        self.assertEqual(meta["duration_seconds"], 120.0)
        self.assertEqual(self._queued(app), ["longa"])

    def test_readota_failed_por_eacces_do_addons(self):
        """#167: reunião 'falhada' pela instalação de componentes (EACCES no
        addons) é transitória por definição - volta na varredura; failed por
        outra causa continua terminal."""
        _meeting(self.root, "vitima", {"status": "failed", "error":
                 "PermissionError: [Errno 13] Permission denied: "
                 "'C:\\\\Users\\\\x\\\\AppData\\\\Local\\\\ScribaDev\\\\addons"
                 "\\\\typing_extensions.py'"})
        _meeting(self.root, "quebrada", {"status": "failed",
                                         "error": "ValueError: boom"})
        app = self._app()
        app.scan_pending()
        self.assertEqual(self._queued(app), ["vitima"])

    def test_processamento_adiado_durante_instalacao(self):
        """#167: com o marcador .installing ativo, o worker NÃO sobe o
        subprocesso - a reunião fica como está para a varredura readotar."""
        d = _meeting(self.root, "m1", {"status": "recorded"})
        app = self._app()
        app.ui = lambda f: f()
        app._hide_pill_if_processing = lambda: None
        with mock.patch("scriba.addons.is_installing", return_value=True), \
                mock.patch("subprocess.Popen") as popen:
            app._process_subprocess(d)
        popen.assert_not_called()
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "recorded")   # intacta, readotável

    def _run_com_rc(self, folder, rc: int, saida: str = ""):
        """Roda _process_subprocess com o subprocesso FALSO saindo com `rc`."""
        from scriba import util

        app = self._app()
        app.ui = lambda f: f()
        app._hide_pill_if_processing = lambda: None
        app._pill_processing = lambda _s: None
        if saida:
            (folder / "process.log").write_text(saida, encoding="utf-8")
        proc = mock.Mock()
        proc.poll.return_value = rc
        proc.returncode = rc
        with mock.patch("scriba.addons.is_installing", return_value=False), \
                mock.patch.object(util, "app_command", return_value=["x"]), \
                mock.patch("subprocess.Popen", return_value=proc):
            app._process_subprocess(folder)
        return app, json.loads((folder / "meta.json").read_text(encoding="utf-8"))

    def test_rc_de_componentes_danificados_vira_mensagem_acionavel(self):
        """#170: em vez do traceback cru de PermissionError, o usuário recebe o
        que fazer - e o toast aponta o reparo, não um retry que vai falhar igual."""
        from scriba import addons

        d = _meeting(self.root, "m1", {"status": "transcribing"})
        app, meta = self._run_com_rc(d, 4)
        self.assertEqual(meta["status"], "failed")
        self.assertEqual(meta["error"], addons.DAMAGED_HINT)
        titulo, corpo = app._toast.call_args[0]
        self.assertIn("componentes danificados", titulo.casefold())
        self.assertIn("Configurações", corpo)

    def test_dano_detectado_tambem_pelo_texto_do_log(self):
        """Versão antiga da CLI (sem o rc 4) ou erro por outro caminho: o texto
        do process.log denuncia o addons e o tratamento é o mesmo."""
        from scriba import addons

        d = _meeting(self.root, "m2", {"status": "transcribing"})
        tail = (r"PermissionError: [Errno 13] Permission denied: "
                r"'C:\Users\x\AppData\Local\ScribaDev\addons\typing_extensions.py'")
        _app, meta = self._run_com_rc(d, 1, saida=tail)
        self.assertEqual(meta["error"], addons.DAMAGED_HINT)

    def test_falha_comum_mantem_o_erro_cru_e_o_toast_de_retry(self):
        d = _meeting(self.root, "m3", {"status": "transcribing"})
        app, meta = self._run_com_rc(d, 1, saida="ValueError: audio corrompido")
        self.assertIn("audio corrompido", meta["error"])
        self.assertIn("falha ao processar", app._toast.call_args[0][0].casefold())


@unittest.skipUnless(_HAVE_PIPELINE, "scriba.pipeline indisponível (deps de transcrição)")
class ProcessWhenReadyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="scriba_pwr_"))

    def test_readota_diarizing_preso_sem_lock(self):
        # regressão #89: caía no caminho de "órfã" e retornava sem processar
        d = _meeting(self.root, "m1", {"status": "diarizing"})
        with mock.patch("scriba.pipeline.process_folder", return_value=True) as pf:
            rc = pipeline.process_when_ready(d, poll_seconds=0.01)
        self.assertEqual(rc, 0)
        pf.assert_called_once_with(d)

    def test_nao_readota_diarizing_com_lock_ativo(self):
        # .lock vivo = worker em andamento: espera e desiste como órfã, sem processar
        d = _meeting(self.root, "m1", {"status": "diarizing"})
        (d / ".lock").write_text(
            json.dumps({"pid": os.getpid(), "started": time.time()}), encoding="utf-8"
        )
        clock = {"t": 0.0}

        def _mono():
            clock["t"] += 70.0  # 2 ticks parados > 120 s → desiste
            return clock["t"]

        fake_time = SimpleNamespace(monotonic=_mono, sleep=lambda s: None, time=time.time)
        with mock.patch("scriba.pipeline.process_folder") as pf, \
                mock.patch("scriba.pipeline.time", fake_time):
            rc = pipeline.process_when_ready(d, poll_seconds=0.01)
        self.assertEqual(rc, 0)
        pf.assert_not_called()

    def test_terminal_nao_processa(self):
        d = _meeting(self.root, "m1", {"status": "too_short"})
        with mock.patch("scriba.pipeline.process_folder") as pf:
            rc = pipeline.process_when_ready(d, poll_seconds=0.01)
        self.assertEqual(rc, 0)
        pf.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
