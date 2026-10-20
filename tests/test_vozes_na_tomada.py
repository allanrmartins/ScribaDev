"""Separar vozes só na tomada (diarization.only_on_ac, 2026-10-20).

Na bateria a nota sai na hora sem separar vozes (meta `diarization_deferred`) e o
app reprocessa sozinho quando o notebook volta à tomada. Cobre: a sonda de energia
(contrato "na dúvida, tomada"), a config, o pipeline e a vigia do app.
"""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import power  # noqa: E402

try:
    import scriba.pipeline as pipeline
    _HAVE_PIPELINE = True
except Exception:  # deps pesadas ausentes
    _HAVE_PIPELINE = False


class SondaDeEnergiaTests(unittest.TestCase):
    def test_pmset_so_battery_power_conta_como_bateria(self):
        self.assertTrue(power._parse_pmset(
            "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t80%; discharging;"))
        self.assertFalse(power._parse_pmset(
            "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged;"))
        self.assertFalse(power._parse_pmset("Now drawing from 'UPS Power'"))
        self.assertFalse(power._parse_pmset(""))

    def _sysfs(self, fontes: dict) -> Path:
        root = Path(tempfile.mkdtemp(prefix="scriba_ps_"))
        for nome, campos in fontes.items():
            (root / nome).mkdir()
            for k, v in campos.items():
                (root / nome / k).write_text(v + "\n")
        return root

    def test_linux_bateria_descarregando_sem_fonte_externa(self):
        root = self._sysfs({"AC": {"type": "Mains", "online": "0"},
                            "BAT0": {"type": "Battery", "status": "Discharging"}})
        self.assertTrue(power._linux_on_battery(root))

    def test_linux_com_fonte_online_e_tomada(self):
        root = self._sysfs({"AC": {"type": "Mains", "online": "1"},
                            "BAT0": {"type": "Battery", "status": "Charging"}})
        self.assertFalse(power._linux_on_battery(root))

    def test_desktop_sem_bateria_e_tomada(self):
        self.assertFalse(power._linux_on_battery(self._sysfs({})))
        self.assertFalse(power._linux_on_battery(Path("/nao/existe")))

    def test_sonda_quebrada_conta_como_tomada(self):
        with mock.patch.object(sys, "platform", "darwin"), \
                mock.patch.object(power, "_mac_on_battery", side_effect=OSError("sem pmset")):
            self.assertFalse(power.on_battery())


class ConfigTests(unittest.TestCase):
    def setUp(self):
        from scriba import util

        self._orig = (util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH)
        d = Path(tempfile.mkdtemp(prefix="scriba_ac_"))
        util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH = d, d / "logs", d / "config.toml"

    def tearDown(self):
        from scriba import util

        util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH = self._orig

    def test_ligada_por_padrao_e_sobrevive_ao_save(self):
        import dataclasses

        from scriba import config

        cfg = config.load()
        self.assertTrue(cfg.diarization.only_on_ac)
        config.save(dataclasses.replace(
            cfg, diarization=dataclasses.replace(cfg.diarization, only_on_ac=False)))
        self.assertFalse(config.load().diarization.only_on_ac)


@unittest.skipUnless(_HAVE_PIPELINE, "scriba.pipeline indisponível (deps de transcrição)")
class PipelineTests(unittest.TestCase):
    def _run(self, *, battery: bool, only_on_ac: bool = True):
        from scriba.transcriber import Segment

        d = Path(tempfile.mkdtemp(prefix="scriba_pl_ac_"))
        (d / "meta.json").write_text(json.dumps({
            "status": "recorded",
            "streams": {"loopback": {"file": "loopback.wav", "offset_seconds": 0.0}},
        }), encoding="utf-8")
        (d / "loopback.wav").write_bytes(b"\x00" * 80)

        class _FakeTr:
            device_used = "cpu"

            def ensure_loaded(self):
                return "cpu"

            def transcribe(self, wav):
                return [Segment(0.0, 1.0, "olá")]

            def close(self):
                pass

        cfg = SimpleNamespace(
            whisper=SimpleNamespace(engine="local", model="fake"),
            diarization=SimpleNamespace(enabled=True, only_on_ac=only_on_ac, model="fake"))
        with mock.patch.object(pipeline, "load", return_value=cfg), \
                mock.patch.object(pipeline.power, "on_battery", return_value=battery), \
                mock.patch("scriba.diarize.diarize", return_value=None) as dz:
            rc = pipeline.transcribe_folder(d, transcriber=_FakeTr())
        self.assertEqual(rc, 0)
        return json.loads((d / "meta.json").read_text(encoding="utf-8")), dz

    def test_na_bateria_adia_as_vozes_e_a_transcricao_segue(self):
        meta, dz = self._run(battery=True)
        dz.assert_not_called()
        self.assertEqual(meta["diarization_deferred"], "battery")
        self.assertEqual(meta["status"], "transcribed")
        self.assertNotIn("diarization_error", meta)   # adiar não é erro (nem toast de erro)

    def test_na_tomada_separa_as_vozes(self):
        meta, dz = self._run(battery=False)
        dz.assert_called_once()
        self.assertNotIn("diarization_deferred", meta)

    def test_opcao_desligada_separa_mesmo_na_bateria(self):
        _meta, dz = self._run(battery=True, only_on_ac=False)
        dz.assert_called_once()


class VigiaDoAppTests(unittest.TestCase):
    """ScribaApp via __new__ (sem __init__): nada de Qt, áudio ou detector real."""

    def _app(self, rec_dir: Path):
        from scriba.main import ScribaApp

        app = ScribaApp.__new__(ScribaApp)
        app.cfg = SimpleNamespace(
            diarization=SimpleNamespace(enabled=True),
            output=SimpleNamespace(resolved_recordings_dir=lambda: rec_dir))
        app._reprocess_queued = set()
        app._toast = mock.Mock()
        app.enqueue_reprocess = mock.Mock(
            side_effect=lambda f, verb, toast=True: app._reprocess_queued.add(str(f)))
        app.stop_event = threading.Event()
        return app

    def _reuniao(self, root: Path, nome: str, meta: dict) -> Path:
        d = root / "2026" / "10" / "20" / nome
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return d

    def test_reprocessa_so_as_adiadas_e_prontas(self):
        root = Path(tempfile.mkdtemp(prefix="scriba_rec_"))
        adiada = self._reuniao(root, "10-59", {"status": "done", "diarization_deferred": "battery"})
        self._reuniao(root, "13-30", {"status": "done"})
        self._reuniao(root, "14-00", {"status": "transcribing", "diarization_deferred": "battery"})
        app = self._app(root)
        app._reprocess_deferred_voices()
        app.enqueue_reprocess.assert_called_once_with(adiada, "process", toast=False)
        app._toast.assert_called_once()
        # 2ª volta à tomada com a 1ª ainda na fila: não duplica
        app._reprocess_deferred_voices()
        app.enqueue_reprocess.assert_called_once()

    def test_vigia_dispara_so_na_transicao_bateria_para_tomada(self):
        app = self._app(Path(tempfile.mkdtemp()))
        app._reprocess_deferred_voices = mock.Mock()
        app.stop_event = mock.Mock()
        app.stop_event.wait.side_effect = [False] * 5 + [True]   # 5 sondas e encerra
        # boot (tomada), tira da tomada, segue na bateria, volta, segue na tomada
        with mock.patch.object(power, "on_battery", side_effect=[False, True, True, False, False]):
            app._power_watch()
        self.assertEqual(app._reprocess_deferred_voices.call_count, 2)   # boot + volta


if __name__ == "__main__":
    unittest.main()
