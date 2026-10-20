"""Motor de fala da Apple (#222): seleção, resultado ao vivo, fallback e ganchos.

O helper Swift real só roda no macOS 26+ (teste de integração no fim, pulado fora
dele); o resto simula a saída JSON Lines do helper.
"""

import dataclasses
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import stt_apple, transcription  # noqa: E402
from scriba.config import Whisper  # noqa: E402

_JSONL = [json.dumps({"start": 0.5, "end": 2.0, "text": "bom dia"}),
          json.dumps({"start": 2.5, "end": 4.0, "text": "vamos lá"}),
          json.dumps({"done": True})]


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="scriba_apple_"))


class ParseTests(unittest.TestCase):
    def test_trechos_e_fim(self):
        segs, done = stt_apple._parse(_JSONL)
        self.assertTrue(done)
        self.assertEqual([(s.start, s.end, s.text) for s in segs],
                         [(0.5, 2.0, "bom dia"), (2.5, 4.0, "vamos lá")])

    def test_linha_truncada_e_ignorada(self):
        segs, done = stt_apple._parse([_JSONL[0], '{"start": 3', ""])
        self.assertEqual(len(segs), 1)
        self.assertFalse(done)


class LiveResultTests(unittest.TestCase):
    def test_sem_arquivo_ao_vivo(self):
        self.assertIsNone(stt_apple.live_result(_tmp() / "mic.wav"))

    def test_ao_vivo_terminado_vale_tambem_p_o_audio_arquivado(self):
        d = _tmp()
        (d / "loopback.live.jsonl").write_text("\n".join(_JSONL), encoding="utf-8")
        for nome in ("loopback.wav", "loopback.opus"):   # reprocesso usa o .opus
            segs = stt_apple.live_result(d / nome, wait_s=0)
            self.assertEqual(len(segs), 2)

    def test_incompleto_desiste_no_prazo(self):
        d = _tmp()
        (d / "mic.live.jsonl").write_text(_JSONL[0], encoding="utf-8")
        self.assertIsNone(stt_apple.live_result(d / "mic.wav", wait_s=0))

    def test_helper_com_erro_desiste_na_hora(self):
        d = _tmp()
        (d / "mic.live.jsonl").write_text("", encoding="utf-8")
        (d / "mic.live.log").write_text("erro: idioma sem suporte\n", encoding="utf-8")
        with mock.patch.object(stt_apple.time, "sleep") as dorme:
            self.assertIsNone(stt_apple.live_result(d / "mic.wav", wait_s=30))
        dorme.assert_not_called()


class EscolhaDoMotorTests(unittest.TestCase):
    def test_apple_so_no_macos_26(self):
        cfg = Whisper(engine="apple")
        with mock.patch.object(stt_apple, "macos_suportado", return_value=True):
            self.assertEqual(transcription.effective_engine(cfg), "apple")
            self.assertNotEqual(transcription.effective_engine(cfg, force_cpu=True), "apple")
        with mock.patch.object(stt_apple, "macos_suportado", return_value=False):
            self.assertNotEqual(transcription.effective_engine(cfg), "apple")

    def test_macos_suportado(self):
        with mock.patch.object(sys, "platform", "darwin"):
            with mock.patch("platform.mac_ver", return_value=("26.1", ("", "", ""), "arm64")):
                self.assertTrue(stt_apple.macos_suportado())
            with mock.patch("platform.mac_ver", return_value=("15.6", ("", "", ""), "arm64")):
                self.assertFalse(stt_apple.macos_suportado())
        with mock.patch.object(sys, "platform", "win32"):
            self.assertFalse(stt_apple.macos_suportado())

    def test_indisponivel_cai_para_o_whisper(self):
        fallback = mock.Mock()
        fallback.ensure_loaded.return_value = "metal"
        fallback.transcribe.return_value = ["whisper"]
        p = stt_apple.AppleSpeechProvider(Whisper(engine="apple"), lambda: fallback)
        with mock.patch.object(stt_apple, "check", return_value=(False, "sem macOS 26")), \
                mock.patch("builtins.print"):
            self.assertEqual(p.ensure_loaded(), "metal")
            self.assertEqual(p.transcribe(_tmp() / "mic.wav"), ["whisper"])

    def test_falha_no_arquivo_cai_para_o_whisper(self):
        fallback = mock.Mock()
        fallback.ensure_loaded.return_value = "metal"
        fallback.transcribe.return_value = ["whisper"]
        p = stt_apple.AppleSpeechProvider(Whisper(engine="apple"), lambda: fallback)
        p._helper = Path("/nao/existe/apple-stt")
        r = subprocess.CompletedProcess([], 1, stdout="", stderr="erro: quebrou\n")
        with mock.patch.object(subprocess, "run", return_value=r), mock.patch("builtins.print"):
            self.assertEqual(p.transcribe(_tmp() / "mic.wav"), ["whisper"])

    def test_usa_o_resultado_ao_vivo_sem_rodar_o_helper(self):
        d = _tmp()
        (d / "mic.live.jsonl").write_text("\n".join(_JSONL), encoding="utf-8")
        p = stt_apple.AppleSpeechProvider(Whisper(engine="apple"), mock.Mock())
        p._helper = Path("/nao/existe/apple-stt")
        with mock.patch.object(subprocess, "run") as run, mock.patch("builtins.print"):
            segs = p.transcribe(d / "mic.wav")
        run.assert_not_called()
        self.assertEqual(len(segs), 2)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        from scriba import util

        self._orig = (util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH)
        d = _tmp()
        util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH = d, d / "logs", d / "config.toml"

    def tearDown(self):
        from scriba import util

        util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH = self._orig

    def test_ao_vivo_desligado_por_padrao_e_sobrevive_ao_save(self):
        from scriba import config

        cfg = config.load()
        self.assertFalse(cfg.whisper.live)
        config.save(dataclasses.replace(
            cfg, whisper=dataclasses.replace(cfg.whisper, engine="apple", live=True)))
        got = config.load().whisper
        self.assertEqual((got.engine, got.live), ("apple", True))


class GanchosDoAppTests(unittest.TestCase):
    """ScribaApp via __new__ (sem __init__): nada de Qt, áudio ou detector real."""

    def _app(self, engine="apple", live=True):
        from scriba.main import ScribaApp

        app = ScribaApp.__new__(ScribaApp)
        app.cfg = SimpleNamespace(whisper=SimpleNamespace(engine=engine, live=live, language="pt"))
        return app

    def test_liga_so_com_motor_apple_e_ao_vivo(self):
        for engine, live, liga in (("apple", True, True), ("apple", False, False),
                                   ("local", True, False)):
            rec = SimpleNamespace(folder=_tmp())
            with mock.patch.object(stt_apple, "macos_suportado", return_value=True), \
                    mock.patch.object(stt_apple, "LiveSession") as sess:
                self._app(engine, live)._start_live_transcription(rec)
            self.assertEqual(sess.called, liga, (engine, live))

    def test_parar_finaliza_e_descartar_mata(self):
        app = self._app()
        rec = SimpleNamespace(live=mock.Mock())
        app._end_live_transcription(rec)
        rec.live.stop.assert_called_once()
        app._end_live_transcription(rec, discard=True)
        rec.live.kill.assert_called_once()

    def test_falha_no_ao_vivo_nunca_atrapalha_a_gravacao(self):
        rec = SimpleNamespace(folder=_tmp())
        with mock.patch.object(stt_apple, "macos_suportado", return_value=True), \
                mock.patch.object(stt_apple, "LiveSession", side_effect=OSError("boom")), \
                self.assertLogs("scriba", "ERROR"):
            self._app()._start_live_transcription(rec)   # não levanta
        rec.live = mock.Mock(**{"stop.side_effect": OSError("boom")})
        with self.assertLogs("scriba", "ERROR"):
            self._app()._end_live_transcription(rec)


class LiveSessionTests(unittest.TestCase):
    def test_parar_antes_de_subir_nem_sobe_os_helpers(self):
        """Gravação curtíssima: o stop chega enquanto o helper ainda compila - nada
        sobe (sem .live.jsonl, o pipeline transcreve o arquivo, em segundos)."""
        liberar = threading.Event()
        proc = mock.Mock()

        def helper_lento():
            liberar.wait(5)
            return Path("/bin/cat")

        with mock.patch.object(stt_apple, "helper_path", side_effect=helper_lento), \
                mock.patch.object(subprocess, "Popen", return_value=proc) as popen:
            s = stt_apple.LiveSession(_tmp(), "pt")
            s.stop()
            liberar.set()
            for t in threading.enumerate():
                if t.name == "apple-live":
                    t.join(5)
        popen.assert_not_called()


@unittest.skipUnless(stt_apple.macos_suportado(), "motor da Apple só no macOS 26+")
class HelperRealTests(unittest.TestCase):
    """Integração: compila o AppleSTT.swift e consulta o motor de verdade."""

    def test_check_em_portugues(self):
        from scriba import util

        orig = util.APP_DIR
        util.APP_DIR = _tmp()
        try:
            ok, detalhe = stt_apple.check("pt")
        finally:
            util.APP_DIR = orig
        if not ok and "Command Line Tools" in detalhe:
            self.skipTest(detalhe)
        self.assertTrue(ok, detalhe)
        self.assertIn("pt-BR", detalhe)


if __name__ == "__main__":
    unittest.main()
