"""Testes do wizard de 1º uso (#147): fluxo Expressa/Avançada, skip de vozes,
persistência no config e gate should_run. Sonda/downloads mockados (determinístico,
sem rede); Qt offscreen; APP_DIR/CONFIG_PATH isolados em tempdir.

Roda sem dependências externas:  python -m unittest discover -s tests
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None

from scriba import sysprobe, util  # noqa: E402


def _probe_gpu() -> sysprobe.Probe:
    return sysprobe.Probe(cpu_cores=16, ram_gb=32, disk_free_gb=100,
                          gpu_nvidia=True, vram_mb=8192)


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class SetupWizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_wiz_"))
        self._saved = (util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH, util.STATE_PATH)
        util.APP_DIR = self.tmp
        util.LOGS_DIR = self.tmp / "logs"
        util.CONFIG_PATH = self.tmp / "config.toml"
        util.STATE_PATH = self.tmp / "state.json"
        # sonda determinística: máquina com GPU boa
        from scriba.qt import setup_wizard as swz

        self._probe0, self._rec0 = sysprobe.probe, None
        sysprobe.probe = _probe_gpu
        self.swz = swz

    def tearDown(self):
        util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH, util.STATE_PATH = self._saved
        sysprobe.probe = self._probe0
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _win(self, no_downloads=True):
        self.swz.SetupWizardWindow._inline_bg = True  # classe: vale já na sonda do __init__
        self.addCleanup(lambda: setattr(self.swz.SetupWizardWindow, "_inline_bg", False))
        w = self.swz.SetupWizardWindow()
        if no_downloads:
            w._download_worker = lambda: w._done_dl.emit(True, "")
        return w

    # -- sonda + recomendação na página 1 ------------------------------------
    def test_probe_prefill(self):
        w = self._win()
        self.assertIn("GPU NVIDIA", w._machine_box.text())
        self.assertTrue(w._model_radios["large-v3-turbo"].isChecked())
        self.assertIn("recomendado", w._model_radios["large-v3-turbo"].text())
        self.assertEqual(w.rec.diarization, "recomendada")

    # -- fluxo Expressa: pula a página de modelo, cai em Vozes ----------------
    def test_expressa_pula_modelo_e_mostra_vozes(self):
        w = self._win()
        w._rb_express.setChecked(True)
        w._from_machine()
        self.assertEqual(w._stack.currentIndex(), 2)  # Vozes (termos são manuais)

    def test_avancada_passa_pela_pagina_de_modelo(self):
        w = self._win()
        w._rb_custom.setChecked(True)
        w._from_machine()
        self.assertEqual(w._stack.currentIndex(), 1)

    # -- vozes: skip e aceite -------------------------------------------------
    def test_skip_vozes_nao_ativa_diarizacao(self):
        w = self._win()
        w._from_machine()
        w._skip_voices()
        self.qapp.processEvents()
        self.assertEqual(w._stack.currentIndex(), 4)  # downloads mock → Pronto
        cfg = self._reload_cfg()
        self.assertFalse(cfg.diarization.enabled)
        self.assertIn("pulada", w._ready_box.text())

    def test_aceitar_vozes_grava_token_e_ativa(self):
        w = self._win()
        w._from_machine()
        w._hf_token.setText("hf_teste123")
        w._accept_voices()
        self.qapp.processEvents()
        cfg = self._reload_cfg()
        self.assertTrue(cfg.diarization.enabled)
        self.assertEqual(cfg.diarization.hf_token, "hf_teste123")
        self.assertIn("ativada", w._ready_box.text())

    def test_config_recebe_modelo_recomendado(self):
        w = self._win()
        w._from_machine()
        w._skip_voices()
        self.qapp.processEvents()
        cfg = self._reload_cfg()
        self.assertEqual(cfg.whisper.model, "large-v3-turbo")
        self.assertEqual(cfg.whisper.device, "auto")

    # -- gate + estado --------------------------------------------------------
    def test_finish_marca_done_e_should_run(self):
        from scriba import updates

        w = self._win()
        self.assertFalse(self.swz.is_done())
        w._finish()
        self.assertTrue(self.swz.is_done())
        # gate: só congelado E não-concluído
        sys.frozen = True
        try:
            self.assertFalse(self.swz.should_run())      # done
            util.STATE_PATH.unlink(missing_ok=True)
            self.assertTrue(self.swz.should_run())       # congelado sem wizard
        finally:
            del sys.frozen
        self.assertFalse(self.swz.should_run())          # fonte: nunca

    def _reload_cfg(self):
        from scriba import config as config_mod

        return config_mod.load()

    # -- % real da barra (#147): plano ponderado + medição de diretório -------
    def test_plan_items_pondera_por_mb(self):
        w = self._win()
        w._from_machine()
        # com vozes: modelo + voices (cuda só em instalação congelada)
        w.skip_voices = False
        items = {k: mb for k, _l, mb in w._plan_items()}
        self.assertIn("model", items)
        self.assertIn("voices", items)
        self.assertNotIn("cuda", items)     # não-congelado: sem download de CUDA
        self.assertEqual(items["model"],
                         sysprobe.MODEL_DOWNLOAD_MB[w._selected_model()])
        # pulando as vozes, o item some e o total encolhe
        w.skip_voices = True
        self.assertNotIn("voices", [k for k, _l, _mb in w._plan_items()])

    def test_dir_mb_mede_e_degrada(self):
        from scriba.qt.setup_wizard import SetupWizardWindow as W

        d = self.tmp / "cache"
        d.mkdir()
        (d / "blob.bin").write_bytes(b"x" * 1048576)   # 1 MB
        self.assertAlmostEqual(W._dir_mb(d), 1.0, places=2)
        self.assertEqual(W._dir_mb(self.tmp / "nao-existe"), 0.0)

    def test_barra_avanca_com_progress_frac(self):
        w = self._win()
        w._progress_frac.emit(500)
        self.qapp.processEvents()
        self.assertEqual(w._bar.value(), 500)
        self.assertEqual(w._bar.maximum(), 1000)


class AddonsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_addons_"))
        self._app0 = util.APP_DIR
        util.APP_DIR = self.tmp

    def tearDown(self):
        util.APP_DIR = self._app0
        from scriba import addons

        p = str(self.tmp / "addons")
        if p in sys.path:
            sys.path.remove(p)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bootstrap_so_no_congelado_e_com_pasta(self):
        from scriba import addons

        self.assertFalse(addons.bootstrap())          # fonte: no-op
        sys.frozen = True
        try:
            self.assertFalse(addons.bootstrap())      # sem a pasta: no-op
            (self.tmp / "addons").mkdir()
            self.assertTrue(addons.bootstrap())       # adiciona
            self.assertIn(str(self.tmp / "addons"), sys.path)
            self.assertFalse(addons.bootstrap())      # idempotente
        finally:
            del sys.frozen


if __name__ == "__main__":
    unittest.main()
