"""Testes de scriba.diagnostics: redação de segredos, tail do log e reunião com falha.

Sem tkinter — só a lógica pura. Roda sem dependências: python -m unittest discover -s tests
"""

import json
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import diagnostics as dg  # noqa: E402


class RedactTests(unittest.TestCase):
    def test_redige_segredos_preserva_resto(self):
        cfg = (
            "[diarization]\n"
            'hf_token = "ENC:abc123segredo"\n'
            "enabled = true\n"
            "[whisper]\n"
            'cloud_api_key = "ENC:xyz"\n'
            "model = \"large-v3-turbo\"\n"
            "[summary]\n"
            'api_key = "ENC:zzz"\n'
        )
        out = dg.redact_config(cfg)
        self.assertNotIn("segredo", out)
        self.assertNotIn("ENC:xyz", out)
        self.assertNotIn("ENC:zzz", out)
        self.assertIn('hf_token = "***"', out.replace("  ", " "))  # tolera espaçamento
        # não-segredos intactos
        self.assertIn("enabled = true", out)
        self.assertIn('model = "large-v3-turbo"', out)

    def test_cloud_api_key_nao_confunde_com_api_key(self):
        out = dg.redact_config('cloud_api_key = "ENC:aaa"\n')
        self.assertIn("cloud_api_key", out)
        self.assertNotIn("ENC:aaa", out)

    def test_vazio(self):
        self.assertEqual(dg.redact_config(""), "")


class ScrubberTests(unittest.TestCase):
    """Ofuscação de dados sensíveis no diagnóstico: tokens estáveis, vocabulário
    das bases locais (read-only, toleradas ausentes) e regras de linha."""

    def test_tokens_estaveis_e_caixa_insensivel(self):
        s = dg.Scrubber()
        s.add("Coruripe", "cliente")
        s.add("Guilherme Lima", "pessoa")
        out = s.scrub("CORURIPE ligou; Coruripe de novo; Guilherme Lima saiu")
        self.assertNotIn("oruripe", out)
        self.assertNotIn("Guilherme", out)
        self.assertEqual(out.count("[cliente-1]"), 2)   # MESMO token, correlacionável
        self.assertIn("[pessoa-1]", out)

    def test_valor_longo_ganha_do_curto_e_borda_de_palavra(self):
        s = dg.Scrubber()
        s.add("Coruripe", "cliente")
        s.add("Usina Coruripe", "cliente")
        self.assertEqual(s.scrub("na Usina Coruripe hoje"), "na [cliente-2] hoje")
        s.add("ACA", "cliente")
        self.assertEqual(s.scrub("a vaca e a ACA"), "a vaca e a [cliente-3]")
        s.add("PX", "cliente")                          # curto demais: ignorado
        self.assertEqual(s.scrub("100px de PX"), "100px de PX")

    def test_usuario_em_caminhos_e_regras_de_linha(self):
        s = dg.Scrubber()
        out = s.scrub(r"log em C:\Users\Dittrichi\AppData e /home/allan/x")
        self.assertNotIn("Dittrichi", out)
        self.assertNotIn("allan", out)
        self.assertEqual(out.count("[usuario]"), 2)
        out = s.scrub("09:12 vozes reconhecidas: Participante 1 -> Raul, 2 -> Marcão\n"
                      "WARNING possível nova call sem ciclo de mic: Suzi — Abaco BR\n")
        self.assertNotIn("Raul", out)
        self.assertNotIn("Suzi", out)
        self.assertIn("vozes reconhecidas: [pessoas]", out)
        self.assertIn("possível nova call sem ciclo de mic: [janela]", out)

    def test_vocabulario_das_bases_locais_read_only(self):
        import sqlite3

        from scriba import util

        tmp = Path(tempfile.mkdtemp(prefix="scriba_scrub_"))
        saved = util.APP_DIR
        util.APP_DIR = tmp
        try:
            with sqlite3.connect(tmp / "index.db") as c:
                c.execute("CREATE TABLE meetings (folder TEXT, title TEXT, client TEXT,"
                          " meeting_title TEXT, export_path TEXT)")
                c.execute("CREATE TABLE participants (name TEXT)")
                c.execute("INSERT INTO meetings VALUES (?,?,?,?,?)",
                          (r"C:\g\2026\08\20\08-10_Alinhamento Coruripe",
                           "Alinhamento Coruripe", "Coruripe", "", ""))
                c.execute("INSERT INTO participants VALUES ('Guilherme Lima')")
            with sqlite3.connect(tmp / "timesheet.db") as c:
                c.execute("CREATE TABLE clients (name TEXT)")
                c.execute("CREATE TABLE client_aliases (alias TEXT)")
                c.execute("CREATE TABLE entries (client_text TEXT)")
                c.execute("INSERT INTO clients VALUES ('Cellera')")
                c.execute("INSERT INTO client_aliases VALUES ('celera farma')")
                c.execute("INSERT INTO entries VALUES ('Usina Coruripe')")
            s = dg.build_scrubber()
            out = s.scrub("pasta renomeada: 08-10_Alinhamento Coruripe; "
                          "cellera e CELERA FARMA; Guilherme Lima; Usina Coruripe")
            for name in ("Coruripe", "ellera", "Guilherme", "Alinhamento"):
                self.assertNotIn(name, out)
            self.assertIn("[reuniao-", out)
            self.assertIn("[pessoa-", out)
            self.assertIn("[cliente-", out)
        finally:
            util.APP_DIR = saved
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bases_ausentes_nao_criam_arquivo(self):
        from scriba import util

        tmp = Path(tempfile.mkdtemp(prefix="scriba_scrub_"))
        saved = util.APP_DIR
        util.APP_DIR = tmp / "nao-existe"
        try:
            s = dg.build_scrubber(tmp / "sem-gravacoes")
            self.assertEqual(s.scrub("texto qualquer"), "texto qualquer")
            self.assertFalse((tmp / "nao-existe").exists())   # dormência preservada
        finally:
            util.APP_DIR = saved
            shutil.rmtree(tmp, ignore_errors=True)


class ErrorTailTests(unittest.TestCase):
    """error_tail: as últimas entradas de ERRO (com traceback) p/ o corpo da issue."""

    _LOG = ("17/08/2026 10:00:00 INFO scriba: subiu\n"
            "17/08/2026 10:00:01 ERROR scriba: mic falhou\n"
            "Traceback (most recent call last):\n"
            "  boom no PortAudio\n"
            "17/08/2026 10:00:02 INFO scriba: seguindo\n"
            "17/08/2026 10:00:03 ERROR scriba: loopback falhou\n")

    def test_pega_so_erros_com_traceback_junto(self):
        out = dg.error_tail(self._LOG)
        self.assertIn("mic falhou", out)
        self.assertIn("boom no PortAudio", out)     # traceback incorporado na entrada
        self.assertIn("loopback falhou", out)
        self.assertNotIn("seguindo", out)           # INFO fica de fora quando há erro

    def test_sem_erros_cai_nas_ultimas_entradas(self):
        out = dg.error_tail("17/08/2026 10:00:00 INFO scriba: a\n"
                            "17/08/2026 10:00:01 INFO scriba: b\n")
        self.assertIn("b", out)

    def test_trunca_no_limite(self):
        out = dg.error_tail("17/08/2026 10:00:00 ERROR scriba: " + "x" * 5000, max_chars=200)
        self.assertLessEqual(len(out), 200)


class ExportZipTests(unittest.TestCase):
    """export_zip TEM de levar todos os .log (pip.log incluso — sem ele o 'pip
    retornou N' do Baixar componentes chega sem o traceback). Home/dirs isolados."""

    def setUp(self):
        import zipfile

        from scriba import util
        from unittest import mock

        self.zipfile = zipfile
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_diagzip_"))
        self._saved = (util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH)
        util.APP_DIR = self.tmp / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        util.CONFIG_PATH = self.tmp / "config.toml"
        util.LOGS_DIR.mkdir(parents=True)
        util.CONFIG_PATH.write_text("[audio]\n", encoding="utf-8")
        (util.LOGS_DIR / "scriba.log").write_text("linha\n", encoding="utf-8")
        (util.LOGS_DIR / "pip.log").write_text("=== pip install\nERROR: x\n", encoding="utf-8")
        self._home = mock.patch("pathlib.Path.home", return_value=self.tmp / "home")
        (self.tmp / "home").mkdir()
        self._home.start()
        self.addCleanup(self._home.stop)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def tearDown(self):
        from scriba import util

        util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH = self._saved

    def test_zip_inclui_pip_log_e_scriba_log(self):
        dest = dg.export_zip(None)
        self.assertTrue(str(dest).endswith(".zip"))
        with self.zipfile.ZipFile(dest) as z:
            names = z.namelist()
        self.assertIn("logs/scriba.log", names)
        self.assertIn("logs/pip.log", names)
        self.assertIn("ambiente.txt", names)
        self.assertIn("OFUSCACAO.txt", names)

    def test_zip_sai_ofuscado(self):
        """Nome de cliente/pessoa/reunião e usuário do SO NÃO saem no zip - nem
        no scriba.log, nem no meta.json/process.log da reunião com falha."""
        from scriba import util

        (util.LOGS_DIR / "scriba.log").write_text(
            "20/08/2026 09:00:00 INFO scriba: concluído 08-10_Call Coruripe -> "
            "C:\\Users\\Fulano\\Notas\\x.md\n"
            "20/08/2026 09:01:00 INFO scriba: vozes reconhecidas: P1 -> Raul\n",
            encoding="utf-8")
        rec = self.tmp / "rec"
        failed = rec / "2026" / "08" / "20" / "08-10_Call Coruripe"
        failed.mkdir(parents=True)
        (failed / "meta.json").write_text(json.dumps(
            {"status": "failed", "title": "Call Coruripe", "client": "Coruripe"},
            ensure_ascii=False), encoding="utf-8")
        (failed / "process.log").write_text("transcrevendo Call Coruripe\n",
                                            encoding="utf-8")
        dest = dg.export_zip(rec)
        with self.zipfile.ZipFile(dest) as z:
            log_txt = z.read("logs/scriba.log").decode("utf-8")
            meta_txt = z.read("reuniao_com_falha/meta.json").decode("utf-8")
            proc_txt = z.read("reuniao_com_falha/process.log").decode("utf-8")
        joined = log_txt + meta_txt + proc_txt
        for name in ("Coruripe", "Fulano", "Raul"):
            self.assertNotIn(name, joined)
        self.assertIn("[usuario]", log_txt)
        self.assertIn("vozes reconhecidas: [pessoas]", log_txt)
        self.assertIn("[reuniao-", log_txt)
        self.assertIn("[cliente-", meta_txt)
        # o que o suporte precisa segue lá: fluxo e timing intactos
        self.assertIn("concluído", log_txt)
        self.assertIn("09:00:00", log_txt)


class ReadTailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_diag_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ultimas_n_linhas(self):
        f = self.tmp / "x.log"
        f.write_text("\n".join(f"linha {i}" for i in range(100)), encoding="utf-8")
        out = dg.read_tail(f, 3)
        self.assertEqual(out.splitlines(), ["linha 97", "linha 98", "linha 99"])

    def test_arquivo_ausente(self):
        self.assertIn("sem log", dg.read_tail(self.tmp / "nao_existe.log", 5))


class LatestFailedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_diag_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, name, status, mtime):
        d = self.tmp / name
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({"status": status}), encoding="utf-8")
        import os
        os.utime(d / "meta.json", (mtime, mtime))
        return d

    def test_pega_a_falha_mais_recente(self):
        self._mk("a", "done", 1000)
        self._mk("b", "failed", 2000)
        novo = self._mk("c", "no_audio", 3000)
        self.assertEqual(dg.latest_failed(self.tmp), novo)

    def test_nenhuma_falha(self):
        self._mk("a", "done", 1000)
        self.assertIsNone(dg.latest_failed(self.tmp))


class ParseEntriesTests(unittest.TestCase):
    def test_traceback_agrupado_na_entrada(self):
        text = (
            "29/06/2026 16:32:46 INFO scriba: iniciado\n"
            "29/06/2026 16:33:01 ERROR scriba.diarize: diarizacao falhou\n"
            "Traceback (most recent call last):\n"
            '  File "x.py", line 1, in <module>\n'
            "ValueError: boom\n"
            "29/06/2026 16:34:00 WARNING scriba: aviso\n"
        )
        e = dg.parse_entries(text)
        self.assertEqual(len(e), 3)  # o traceback foi incorporado à entrada ERROR
        self.assertEqual([x["level"] for x in e], ["INFO", "ERROR", "WARNING"])
        self.assertIn("Traceback", e[1]["text"])
        self.assertIn("ValueError: boom", e[1]["text"])
        self.assertEqual((e[1]["date"], e[1]["time"]), ("29/06/2026", "16:33:01"))

    def test_continuacao_no_inicio(self):
        e = dg.parse_entries("linha sem cabecalho\noutra")
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0]["level"], "")


class FilterEntriesTests(unittest.TestCase):
    def _entries(self):
        return dg.parse_entries(
            "29/06/2026 10:00:00 INFO scriba: a\n"
            "29/06/2026 11:00:00 WARNING scriba: b\n"
            "29/06/2026 12:00:00 ERROR scriba: c\n"
            "30/06/2026 09:00:00 ERROR scriba: d\n"
        )

    def test_nivel_minimo(self):
        e = self._entries()
        self.assertEqual(len(dg.filter_entries(e, level_min=0)), 4)
        self.assertEqual(len(dg.filter_entries(e, level_min=30)), 3)   # avisos+
        self.assertEqual(len(dg.filter_entries(e, level_min=40)), 2)   # só erros

    def test_filtro_dia(self):
        self.assertEqual(len(dg.filter_entries(self._entries(), date_br="30/06/2026")), 1)

    def test_filtro_hora_a_partir_de(self):
        out = dg.filter_entries(self._entries(), date_br="29/06/2026", time_from="11:30")
        self.assertEqual(len(out), 1)               # só a de 12:00
        self.assertEqual(out[0]["level"], "ERROR")


class BugReportUrlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_bug_"))
        self._orig_log = dg.LOG_FILE

    def tearDown(self):
        dg.LOG_FILE = self._orig_log
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _body(self, url: str) -> str:
        qs = urllib.parse.urlparse(url).query
        return urllib.parse.parse_qs(qs)["body"][0]

    def test_url_e_corpo_preenchido(self):
        f = self.tmp / "scriba.log"
        f.write_text("29/06/2026 10:00:00 ERROR scriba: boom_no_log\n", encoding="utf-8")
        dg.LOG_FILE = f
        url = dg.bug_report_url()
        self.assertTrue(url.startswith(dg.GH_NEW_ISSUE + "?"))
        body = self._body(url)
        self.assertIn("## Ambiente", body)
        self.assertIn("## Log", body)
        self.assertIn("boom_no_log", body)

    def test_trunca_mantendo_o_fim(self):
        f = self.tmp / "scriba.log"
        f.write_text("\n".join(f"linha {i:04d} " + "z" * 60 for i in range(3000)), encoding="utf-8")
        dg.LOG_FILE = f
        url = dg.bug_report_url(tail_lines=500, max_url=4000)
        self.assertLessEqual(len(url), 4000)
        body = self._body(url)
        self.assertIn("linha 2999", body)      # a linha mais recente sobrevive
        self.assertNotIn("linha 2500", body)   # as mais antigas do tail são cortadas

    def test_log_ausente_ainda_gera_url(self):
        dg.LOG_FILE = self.tmp / "nao_existe.log"
        url = dg.bug_report_url()
        self.assertTrue(url.startswith(dg.GH_NEW_ISSUE))
        self.assertIn("Ambiente", self._body(url))


if __name__ == "__main__":
    unittest.main(verbosity=2)
