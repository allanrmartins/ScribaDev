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

import itertools
import json
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import main as main_mod  # noqa: E402
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
            timesheet=SimpleNamespace(enabled=False, suggest=False),
        )
        app.jobs = queue.Queue()
        app._reprocess_queued = set()
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

    _ERRO_WD = ("processamento encerrado pelo ScribaDev: ficou 20 min sem nenhum "
                "sinal de progresso (nem CPU, nem log). Detalhes em process.log")

    def test_readota_failed_do_watchdog_uma_unica_vez(self):
        """#186: derrubada pelo watchdog é transitória como a do EACCES (o
        travamento era do AMBIENTE, ex.: o CUDA da #185) - volta UMA vez por
        falha, com carimbo no meta; travar de novo = fica parada, sem laço."""
        d = _meeting(self.root, "derrubada", {
            "status": "failed", "error": self._ERRO_WD,
            "streams": {"mic": {"file": "mic.wav"}}})
        (d / "mic.wav").write_bytes(b"\0" * 100)
        app = self._app()
        app.scan_pending()
        self.assertEqual(self._queued(app), ["derrubada"])
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertTrue(meta["watchdog_requeued"])   # a vez dela já foi gasta
        # segundo boot com a MESMA falha: terminal de verdade
        app2 = self._app()
        app2.scan_pending()
        self.assertEqual(self._queued(app2), [])

    def test_nao_readota_watchdog_sem_audio_utilizavel(self):
        """Re-transcrever sem áudio degradaria a falha para "no_audio" - deixa
        parada (o menu Reprocessar ainda oferece o re-resumo, se houver transcript)."""
        d = _meeting(self.root, "sem_audio", {
            "status": "failed", "error": self._ERRO_WD,
            "streams": {"mic": {"file": "mic.wav"}}})   # o arquivo não existe
        app = self._app()
        app.scan_pending()
        self.assertEqual(self._queued(app), [])
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertNotIn("watchdog_requeued", meta)   # não gastou a vez à toa

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

    def _run_com_rc(self, folder, rc: int, saida: str = "", verb: str = "process"):
        """Roda _process_subprocess com o subprocesso FALSO saindo com `rc`.
        O mock de util.app_command fica em app.cmd_mock (p/ conferir o verbo)."""
        from scriba import util

        app = self._app()
        app.ui = lambda f: f()
        app._hide_pill_if_processing = lambda: None
        app._pill_processing = lambda _s: None
        app._refresh_home_after_process = lambda: None
        if saida:
            (folder / "process.log").write_text(saida, encoding="utf-8")
        proc = mock.Mock()
        proc.poll.return_value = rc
        proc.returncode = rc
        with mock.patch("scriba.addons.is_installing", return_value=False), \
                mock.patch.object(util, "app_command", return_value=["x"]) as cmd, \
                mock.patch("subprocess.Popen", return_value=proc):
            app.cmd_mock = cmd
            app._process_subprocess(folder, verb)
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

    def test_verbo_summarize_monta_o_comando_da_cli(self):
        # #186: o reprocesso barato roda `scriba summarize <pasta>` no subprocesso
        d = _meeting(self.root, "m4", {"status": "done"})
        app, _meta = self._run_com_rc(d, 0, verb="summarize")
        app.cmd_mock.assert_called_once_with("summarize", str(d))

    def test_sucesso_limpa_o_carimbo_do_watchdog(self):
        # #186: episódio fechado - a PRÓXIMA falha de watchdog readota de novo
        d = _meeting(self.root, "m5", {"status": "done", "watchdog_requeued": True})
        _app, meta = self._run_com_rc(d, 0)
        self.assertNotIn("watchdog_requeued", meta)


class ReprocessoManualTests(unittest.TestCase):
    """#186: enqueue_reprocess (o que o menu Reprocessar chama) e o item com
    verbo na fila serial - o worker normaliza e libera o dedup."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="scriba_rp_"))

    def _app(self):
        app = ScribaApp.__new__(ScribaApp)
        app.jobs = queue.Queue()
        app._reprocess_queued = set()
        app._toast = mock.Mock()
        return app

    def test_enfileira_com_verbo_e_limpa_o_carimbo_do_watchdog(self):
        d = _meeting(self.root, "m1", {"status": "done", "watchdog_requeued": True})
        app = self._app()
        with mock.patch("scriba.addons.is_installing", return_value=False):
            app.enqueue_reprocess(d, "summarize")
        self.assertEqual(app.jobs.get_nowait(), (d, "summarize"))
        # intervenção manual zera o episódio: o retry automático volta a valer
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertNotIn("watchdog_requeued", meta)
        self.assertTrue(app.is_reprocess_queued(d))

    def test_clique_duplo_nao_duplica_o_job(self):
        # duas passadas de GPU na mesma reunião por clique duplo = desperdício
        d = _meeting(self.root, "m1", {"status": "done"})
        app = self._app()
        with mock.patch("scriba.addons.is_installing", return_value=False):
            app.enqueue_reprocess(d, "process")
            app.enqueue_reprocess(d, "process")
        app.jobs.get_nowait()
        self.assertTrue(app.jobs.empty())

    def test_instalacao_de_componentes_recusa_com_aviso(self):
        # o worker adiaria e o clique sumiria (done não volta pela varredura):
        # melhor recusar já, com o motivo no toast
        d = _meeting(self.root, "m1", {"status": "done"})
        app = self._app()
        with mock.patch("scriba.addons.is_installing", return_value=True):
            app.enqueue_reprocess(d, "process")
        self.assertTrue(app.jobs.empty())
        self.assertIn("instalação", app._toast.call_args[0][0].casefold())

    def test_worker_normaliza_o_item_e_libera_o_dedup(self):
        d = _meeting(self.root, "m1", {"status": "done"})
        app = self._app()
        app.stop_event = threading.Event()
        app._reprocess_queued = {str(d)}
        app.jobs.put((d, "summarize"))
        feito = threading.Event()
        app._process_subprocess = mock.Mock(side_effect=lambda *_a: feito.set())
        t = threading.Thread(target=app._worker, daemon=True)
        t.start()
        self.assertTrue(feito.wait(timeout=5))
        app.stop_event.set()
        t.join(timeout=5)
        app._process_subprocess.assert_called_once_with(d, "summarize")
        self.assertEqual(app._reprocess_queued, set())


class WatchdogDoSubprocessoTests(unittest.TestCase):
    """#176: filho de processamento que PENDURA congelava a fila inteira - a espera
    não tinha timeout nem watchdog, e a capa seguia mostrando "Transcrevendo…" com a
    máquina a 0%. Agora CPU, meta.json e process.log parados por tempo demais = morto."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="scriba_wd_"))

    def _app(self):
        app = ScribaApp.__new__(ScribaApp)
        app.cfg = SimpleNamespace(summary=SimpleNamespace(timeout_seconds=0))
        app._toast = mock.Mock()
        app.ui = lambda f: f()
        app._hide_pill_if_processing = lambda: None
        app._pill_processing = lambda _s: None
        return app

    def _proc(self, vivo_por: int, rc: int = 1, pid: int = 4242):
        """Popen falso: poll() devolve None nas primeiras `vivo_por` chamadas."""
        proc = mock.Mock()
        proc.pid = pid
        n = {"i": 0}

        def poll():
            n["i"] += 1
            return None if n["i"] <= vivo_por else rc

        proc.poll.side_effect = poll
        proc.returncode = rc
        return proc

    def _rodar(self, folder, proc, cpu):
        from scriba import main as main_mod
        from scriba import util as util_mod

        app = self._app()
        with mock.patch.multiple(main_mod, _SONDA_PROGRESSO_S=0, _TICK_PROCESSAMENTO_S=0,
                                 _PARADO_S=0), \
                mock.patch("scriba.plat.pid_cpu_seconds", side_effect=cpu), \
                mock.patch("scriba.addons.is_installing", return_value=False), \
                mock.patch.object(util_mod, "app_command", return_value=["x"]), \
                mock.patch("subprocess.Popen", return_value=proc):
            app._process_subprocess(folder)
        return app, json.loads((folder / "meta.json").read_text(encoding="utf-8"))

    def test_filho_pendurado_e_encerrado_e_a_reuniao_vira_failed(self):
        d = _meeting(self.root, "presa", {"status": "transcribing"})
        proc = self._proc(vivo_por=50)          # nunca sairia sozinho
        app, meta = self._rodar(d, proc, cpu=lambda _pid: 1.0)   # CPU congelada
        proc.kill.assert_called_once()
        self.assertEqual(meta["status"], "failed")
        self.assertIn("sem nenhum sinal de progresso", meta["error"])
        self.assertIn("travado", app._toast.call_args[0][0].casefold())

    def test_filho_que_queima_cpu_nao_e_morto(self):
        """Falso positivo é o risco real aqui: transcrição longa não pode virar
        'travada' só porque demora. CPU andando = vivo, mesmo sem escrever log."""
        d = _meeting(self.root, "viva", {"status": "transcribing"})
        proc = self._proc(vivo_por=5)
        relogio = itertools.count(0.0, 60.0)    # +60 s de CPU por sondagem
        app, meta = self._rodar(d, proc, cpu=lambda _pid: next(relogio))
        proc.kill.assert_not_called()
        self.assertEqual(meta["status"], "failed")           # pelo rc=1, não pelo watchdog
        self.assertNotIn("sem nenhum sinal", meta.get("error", ""))

    def test_progresso_no_process_log_tambem_conta(self):
        """Onde não há sondagem de CPU (devolve None), o log crescendo basta."""
        d = _meeting(self.root, "baixando", {"status": "transcribing"})
        proc = self._proc(vivo_por=5)
        escrita = {"n": 0}

        def cpu_indisponivel(_pid):
            escrita["n"] += 1
            with (d / "process.log").open("a", encoding="utf-8") as f:
                f.write("baixando modelo %d\n" % escrita["n"])
            return None

        _app, meta = self._rodar(d, proc, cpu=cpu_indisponivel)
        proc.kill.assert_not_called()
        self.assertEqual(meta["status"], "failed")           # rc=1
        self.assertNotIn("sem nenhum sinal", meta.get("error", ""))

    def test_limite_acompanha_o_timeout_do_resumo(self):
        """Gerar resumo é espera de rede legítima (ollama/nuvem/CLI): não queima CPU
        nem escreve log, então o limite nunca pode ser menor que aquele timeout."""
        app = self._app()
        self.assertEqual(app._limite_sem_progresso(), float(main_mod._PARADO_S))
        app.cfg.summary.timeout_seconds = 3600
        self.assertGreaterEqual(app._limite_sem_progresso(), 3600)

    def test_lock_do_filho_morto_e_limpo(self):
        """kill não roda o finally do filho: o .lock dele ficaria para trás e a
        reunião sairia da varredura de pendentes."""
        d = _meeting(self.root, "presa", {"status": "transcribing"})
        (d / ".lock").write_text(json.dumps({"pid": 4242, "started": time.time()}),
                                 encoding="utf-8")
        proc = self._proc(vivo_por=50, pid=4242)
        self._rodar(d, proc, cpu=lambda _pid: 1.0)
        self.assertFalse((d / ".lock").exists())


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
