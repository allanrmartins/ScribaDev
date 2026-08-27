"""Testes de scriba.util: is_locked (#19), atomic_write_text, virtual_screen_rect (#17)."""

import json
import os
import shutil
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

    def test_pid_morto_eh_orfao_mesmo_com_lock_recente(self):
        """#176: lock recente era respeitado SEM olhar o PID - reunião cujo worker
        morreu ficava "Transcrevendo…" e fora da varredura por até 2 h. Quem escreve
        o lock é o próprio filho, então lock com PID morto nunca tem dono vivo."""
        d = _folder(pid=4242, started=time.time())
        with mock.patch("scriba.plat.pid_alive", return_value=False):
            self.assertFalse(util.is_locked(d))

    def test_pid_reciclado_nao_segura_o_lock(self):
        """#176: PID vivo não basta - o Windows recicla números depressa. Processo
        que NASCEU depois do lock é outro dono, e o lock é órfão."""
        d = _folder(pid=4242, started=time.time() - 3600)
        with mock.patch("scriba.plat.pid_alive", return_value=True), \
                mock.patch("scriba.plat.pid_started_at", return_value=time.time()):
            self.assertFalse(util.is_locked(d))

    def test_pid_vivo_do_dono_original_segura_o_lock(self):
        carimbo = time.time() - 3600
        d = _folder(pid=4242, started=carimbo)
        with mock.patch("scriba.plat.pid_alive", return_value=True), \
                mock.patch("scriba.plat.pid_started_at", return_value=carimbo - 5):
            self.assertTrue(util.is_locked(d))

    def test_sem_sondagem_de_nascimento_confia_no_pid_vivo(self):
        """SO sem a sondagem (pid_started_at devolve None): o lock continua valendo
        pelo PID vivo - o teto absoluto é que impede virar eterno."""
        d = _folder(pid=4242, started=time.time() - 3600)
        with mock.patch("scriba.plat.pid_alive", return_value=True), \
                mock.patch("scriba.plat.pid_started_at", return_value=None):
            self.assertTrue(util.is_locked(d))

    def test_teto_absoluto_vence_ate_pid_vivo(self):
        d = _folder(pid=4242, started=time.time() - (util.LOCK_HARD_MAX_AGE_SECONDS + 60))
        with mock.patch("scriba.plat.pid_alive", return_value=True), \
                mock.patch("scriba.plat.pid_started_at", return_value=None):
            self.assertFalse(util.is_locked(d))

    def test_ilegivel_e_velho_nao_segura_para_sempre(self):
        """Lock truncado por queda de energia não pode bloquear a pasta eternamente:
        sem PID para conferir, resta a idade do arquivo."""
        d = Path(tempfile.mkdtemp(prefix="scriba_t_"))
        p = d / ".lock"
        p.write_text("{nao json", encoding="utf-8")
        velho = time.time() - (util.LOCK_MAX_AGE_SECONDS + 60)
        os.utime(p, (velho, velho))
        self.assertFalse(util.is_locked(d))


class ClearLockTests(unittest.TestCase):
    """#176: quem mata um subprocesso travado limpa o lock DELE - e só o dele."""

    def test_apaga_o_lock_do_proprio_pid(self):
        d = _folder(pid=4242, started=time.time())
        self.assertTrue(util.clear_lock(d, 4242))
        self.assertFalse((d / ".lock").exists())

    def test_nao_apaga_lock_de_outro_processo(self):
        d = _folder(pid=4242, started=time.time())
        self.assertFalse(util.clear_lock(d, 9999))
        self.assertTrue((d / ".lock").exists())

    def test_sem_lock_nao_e_erro(self):
        self.assertFalse(util.clear_lock(_folder(), 4242))


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


class AppCommandTests(unittest.TestCase):
    """Subprocessos do app na instalação CONGELADA (#142/#147).

    Um exe do PyInstaller NÃO entende `-m módulo`: `[exe, "-X", "utf8", "-m",
    "scriba.cli", "process", pasta]` chegava inteiro no argparse do app
    ("unrecognized arguments") e NENHUMA gravação era processada na instalação por
    instalador/DMG. O subprocesso tem de ser o exe de console irmão + subcomando.
    """

    def setUp(self):
        # resolve(): /var é symlink p/ /private/var no mac e frozen_console_exe
        # resolve o caminho do bundle (bundles reais podem estar sob symlink)
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_appcmd_")).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        if hasattr(sys, "frozen"):
            self.addCleanup(lambda: setattr(sys, "frozen", True))

    def _finge_bundle(self, nome: str):
        """Cria <tmp>/{ScribaDevApp,<nome>} e aponta o sys.executable p/ o windowed."""
        (self.tmp / "ScribaDevApp").write_text("", encoding="utf-8")
        (self.tmp / nome).write_text("", encoding="utf-8")
        return mock.patch.object(sys, "executable", str(self.tmp / "ScribaDevApp"))

    def test_congelado_usa_o_exe_de_console_irmao_com_subcomando(self):
        nome = "scribadev.exe" if sys.platform == "win32" else "scribadev"
        with mock.patch.object(sys, "frozen", True, create=True), self._finge_bundle(nome):
            self.assertEqual(util.app_command("process", "/pasta"),
                             [str(self.tmp / nome), "process", "/pasta"])
            self.assertEqual(util.frozen_console_exe(), self.tmp / nome)

    def test_congelado_nunca_passa_dash_m(self):
        nome = "scribadev.exe" if sys.platform == "win32" else "scribadev"
        with mock.patch.object(sys, "frozen", True, create=True), self._finge_bundle(nome):
            cmd = util.app_command("process", "/pasta")
        self.assertNotIn("-m", cmd)
        self.assertNotIn("scriba.cli", cmd)

    def test_fonte_mantem_o_dash_m_de_sempre(self):
        cmd = util.app_command("process", "/pasta")
        self.assertEqual(cmd[1:], ["-X", "utf8", "-m", "scriba.cli", "process", "/pasta"])
        self.assertEqual(cmd[0], str(util.console_python()))
        self.assertIsNone(util.frozen_console_exe())

    def test_bundle_sem_o_exe_de_console_cai_no_caminho_de_fonte(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", str(self.tmp / "ScribaDevApp")):
            self.assertIsNone(util.frozen_console_exe())
            self.assertIn("-m", util.app_command("process", "/pasta"))

    def test_sonda_de_audio_vai_pelo_app_command(self):
        with mock.patch.object(util, "app_command",
                               return_value=[sys.executable, "-c", "print('{\"mic\": \"m\"}')"]) as m:
            self.assertEqual(util.run_audio_probe(), {"mic": "m"})
        m.assert_called_once_with("audioprobe")


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


class ProcessingStagesTests(unittest.TestCase):
    """Estágios do processamento (rótulo + fração), fonte da pílula, da aba Notas e da
    faixa 'em andamento' da capa."""

    def test_diarizing_e_um_estagio_proprio(self):
        self.assertEqual(util.stage_label("diarizing"), "Separando vozes…")
        self.assertIn("diarizing", util.IN_PROGRESS_STATUSES)

    def test_ordem_das_fracoes_e_monotonica(self):
        seq = ["recording", "recorded", "transcribing", "diarizing",
               "transcribed", "summarizing", "done"]
        fracs = [util.stage_fraction(s) for s in seq]
        self.assertEqual(fracs, sorted(fracs))          # barra nunca anda p/ trás
        self.assertTrue(0 < util.stage_fraction("diarizing") < 1)

    def test_status_desconhecido_tem_fallback(self):
        self.assertEqual(util.stage_label("xpto"), "Processando…")
        self.assertEqual(util.stage("xpto").icon, "hourglass")
        self.assertFalse(util.stage("xpto").is_error)

    def test_in_progress_deriva_da_tabela(self):
        # #94: IN_PROGRESS_STATUSES é DERIVADO de STAGES (não uma lista à parte que
        # pode ficar pra trás - foi o que causou o #89 do 'diarizing')
        self.assertEqual(
            util.IN_PROGRESS_STATUSES,
            tuple(s for s, st in util.STAGES.items() if st.in_progress))
        for terminal in ("done", "failed", "no_audio"):
            self.assertNotIn(terminal, util.IN_PROGRESS_STATUSES)

    def test_terminais_de_erro(self):
        # #94: is_error marca os terminais de erro (a capa os mostra em vermelho, sem pulso)
        self.assertTrue(util.stage("failed").is_error)
        self.assertTrue(util.stage("no_audio").is_error)
        self.assertFalse(util.stage("summarizing").is_error)
        self.assertFalse(util.stage("done").is_error)

    def test_icones_por_estagio(self):
        # #94: ícone Fluent de cada estágio que aparece na faixa da capa (era _STAGE_ICON)
        esperado = {"recorded": "hourglass", "transcribing": "edit", "diarizing": "people",
                    "transcribed": "checkmark", "summarizing": "sparkle",
                    "failed": "warning", "no_audio": "warning"}
        for status, icon in esperado.items():
            self.assertEqual(util.stage(status).icon, icon)


class HasAudioTests(unittest.TestCase):
    """util.has_audio (#186): decide se "Refazer tudo" habilita no menu Reprocessar
    e se o scan_pending pode readotar uma falha de watchdog (re-transcrever sem
    áudio só degradaria a falha para no_audio)."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="scriba_ha_"))

    def _meta(self, **extra) -> dict:
        meta = {"status": "done", "streams": {"mic": {"file": "mic.wav"}}}
        meta.update(extra)
        (self.d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return meta

    def test_stream_com_wav_gravado(self):
        meta = self._meta()
        (self.d / "mic.wav").write_bytes(b"\0" * 100)
        self.assertTrue(util.has_audio(self.d, meta))

    def test_audio_removed_e_nao_definitivo(self):
        # keep_audio=false: os arquivos foram apagados DE PROPÓSITO
        meta = self._meta(audio_removed=True)
        (self.d / "mic.wav").write_bytes(b"\0" * 100)
        self.assertFalse(util.has_audio(self.d, meta))

    def test_arquivo_apontado_sumiu(self):
        self.assertFalse(util.has_audio(self.d, self._meta()))

    def test_so_header_wav_nao_conta(self):
        meta = self._meta()
        (self.d / "mic.wav").write_bytes(b"\0" * 44)   # header sem uma amostra
        self.assertFalse(util.has_audio(self.d, meta))

    def test_pos_arquivamento_opus_conta(self):
        # archive_format=opus: o meta aponta .opus, não .wav
        meta = self._meta(streams={"mic": {"file": "mic.opus"}})
        (self.d / "mic.opus").write_bytes(b"\0" * 100)
        self.assertTrue(util.has_audio(self.d, meta))

    def test_meta_legado_sem_streams_cai_no_glob(self):
        meta = self._meta(streams={})
        (self.d / "loopback.wav").write_bytes(b"\0" * 100)
        self.assertTrue(util.has_audio(self.d, meta))

    def test_sem_meta_em_maos_le_do_disco(self):
        self._meta()
        (self.d / "mic.wav").write_bytes(b"\0" * 100)
        self.assertTrue(util.has_audio(self.d))


if __name__ == "__main__":
    unittest.main(verbosity=2)
