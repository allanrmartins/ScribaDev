"""Testes de scriba.util: is_locked (#19), atomic_write_text, virtual_screen_rect (#17)."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import util  # noqa: E402


def _folder(**lock):
    d = Path(tempfile.mkdtemp(prefix="scriba_t_"))
    if lock:
        (d / ".lock").write_text(json.dumps(lock), encoding="utf-8")
    return d


class IsLockedTests(unittest.TestCase):
    def test_sem_lock(self):
        self.assertFalse(util.is_locked(_folder()))

    def test_pid_vivo_recente(self):
        self.assertTrue(util.is_locked(_folder(pid=os.getpid(), started=time.time())))

    def test_recente_sem_pid(self):
        self.assertTrue(util.is_locked(_folder(pid=0, started=time.time())))

    def test_antigo_pid_morto_eh_orfao(self):
        old = time.time() - (util.LOCK_MAX_AGE_SECONDS + 1000)
        self.assertFalse(util.is_locked(_folder(pid=0, started=old)))

    def test_ilegivel_assume_ativo(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_t_"))
        (d / ".lock").write_text("{nao json", encoding="utf-8")
        self.assertTrue(util.is_locked(d))


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="scriba_t_"))

    def test_escreve_conteudo(self):
        p = self.d / "x.txt"
        util.atomic_write_text(p, "olá\nmundo")
        self.assertEqual(p.read_text(encoding="utf-8"), "olá\nmundo")

    def test_nao_deixa_tmp(self):
        p = self.d / "x.json"
        util.atomic_write_text(p, "{}")
        self.assertFalse((self.d / "x.json.tmp").exists())

    def test_substitui_existente(self):
        p = self.d / "x.txt"
        p.write_text("velho", encoding="utf-8")
        util.atomic_write_text(p, "novo")
        self.assertEqual(p.read_text(encoding="utf-8"), "novo")


class VirtualScreenTests(unittest.TestCase):
    def test_rect_valido_ou_none(self):
        r = util.virtual_screen_rect()
        self.assertTrue(r is None or (len(r) == 4 and r[2] > 0 and r[3] > 0))


class ResourcePathTests(unittest.TestCase):
    """resource_path: recurso empacotado resolvido de forma compatível com bundle (Fase 0.2)."""

    def tearDown(self):
        if hasattr(sys, "_MEIPASS"):
            del sys._MEIPASS

    def test_modo_fonte_relativo_ao_pacote(self):
        self.assertEqual(util.resource_path("assets"),
                         Path(util.__file__).resolve().parent / "assets")

    def test_assets_e_icones_existem_no_fonte(self):
        self.assertTrue(util.ASSETS_DIR.is_dir())
        self.assertTrue(util.ICON_ICO.exists())

    def test_modo_frozen_usa_meipass(self):
        sys._MEIPASS = r"X:\bundle"
        self.assertEqual(util.resource_path("assets", "scriba.ico"),
                         Path(r"X:\bundle") / "scriba" / "assets" / "scriba.ico")


class FfmpegStatusTests(unittest.TestCase):
    """Regra de diagnóstico do ffmpeg, compartilhada pelo `scriba doctor` e a aba Sobre."""

    def test_presente_eh_ok(self):
        with mock.patch("shutil.which", return_value=r"C:\ffmpeg\bin\ffmpeg.exe"):
            self.assertEqual(util.ffmpeg_status(True, "opus"), "ok")
            self.assertEqual(util.ffmpeg_status(False, "wav"), "ok")

    def test_ausente_querendo_comprimir_eh_err(self):
        # keep_audio + opus/flac: é o caso em que os .wav ficam gigantes
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(util.ffmpeg_status(True, "opus"), "err")
            self.assertEqual(util.ffmpeg_status(True, "flac"), "err")
            self.assertEqual(util.ffmpeg_status(True, "OPUS"), "err")  # case-insensitive

    def test_ausente_sem_impacto_imediato_eh_warn(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(util.ffmpeg_status(False, "opus"), "warn")  # não guarda áudio
            self.assertEqual(util.ffmpeg_status(True, "wav"), "warn")     # formato cru de propósito
            self.assertEqual(util.ffmpeg_status(True, ""), "warn")
            self.assertEqual(util.ffmpeg_status(True, None), "warn")


if __name__ == "__main__":
    unittest.main(verbosity=2)
