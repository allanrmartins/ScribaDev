"""Testes da CLI do apontamento de horas (épico #118, #121).

`scribadev timesheet {list,add,sync,backup}` de ponta a ponta via cli.main(),
com stdout capturado e tudo isolado em tempdir (config, banco e gravações fake).
Foco: gate de dormência (#126), parse/validação dos argumentos, formatação do
list (marcas [?]/[MD], totais), resolução de cliente/projeto no add, contagem
do sync e backup.

Roda sem dependências externas:  python -m unittest discover -s tests
"""

import dataclasses
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import cli, config, util  # noqa: E402
from scriba import timesheet_db as tsdb  # noqa: E402


class TimesheetCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_tscli_"))
        self._app0, self._logs0, self._cfg0, self._db0 = (
            util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH, tsdb.DB_PATH)
        util.APP_DIR = self.tmp / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        util.CONFIG_PATH = util.APP_DIR / "config.toml"
        tsdb.DB_PATH = None  # resolve sob o APP_DIR isolado; apply_config pode mexer

    def tearDown(self):
        util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH, tsdb.DB_PATH = (
            self._app0, self._logs0, self._cfg0, self._db0)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers --------------------------------------------------------------
    def _run(self, *argv) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(list(argv))
        return rc, buf.getvalue()

    def _enable(self, recordings_dir=None):
        cfg = config.load()
        new = dataclasses.replace(
            cfg, timesheet=dataclasses.replace(cfg.timesheet, enabled=True))
        if recordings_dir is not None:
            new = dataclasses.replace(
                new, output=dataclasses.replace(new.output,
                                                recordings_dir=str(recordings_dir)))
        config.save(new)

    def _add(self, **kw):
        base = dict(date="2026-07-13", start="08:00", end="13:00", client="Coruripe")
        base.update(kw)
        argv = ["timesheet", "add", "--date", base["date"], "--start", base["start"],
                "--end", base["end"], "--client", base["client"]]
        if base.get("project"):
            argv += ["--project", base["project"]]
        if base.get("desc"):
            argv += ["--desc", base["desc"]]
        if base.get("extra"):
            argv += ["--extra"]
        return self._run(*argv)

    # -- dormência (#126) ------------------------------------------------------
    def test_dormente_bloqueia_sem_criar_banco(self):
        for sub in (["list"], ["sync"], ["backup"],
                    ["add", "--start", "08:00", "--end", "09:00", "--client", "X"]):
            rc, out = self._run("timesheet", *sub)
            self.assertEqual(rc, 2, sub)
            self.assertIn("não ativado", out)
        self.assertFalse((util.APP_DIR / "timesheet.db").exists())

    # -- add + list -------------------------------------------------------------
    def test_add_e_list_roundtrip(self):
        self._enable()
        rc, out = self._add(desc="aplicacao de notas", extra=True)
        self.assertEqual(rc, 0)
        self.assertIn("aviso: cliente 'Coruripe' não cadastrado", out)
        self.assertIn("08:00-13:00", out)
        rc, out = self._run("timesheet", "list", "--month", "2026-07")
        self.assertEqual(rc, 0)
        self.assertIn("2026-07-13  (5:00)", out)
        self.assertIn("08:00-13:00", out)
        self.assertIn("aplicacao de notas (extra)", out)
        self.assertIn("total 5:00", out)
        self.assertIn("a apontar 5:00", out)

    def test_add_invalido_e_mes_invalido(self):
        self._enable()
        rc, out = self._add(end="07:00")  # fim antes do início
        self.assertEqual(rc, 2)
        self.assertIn("erro:", out)
        rc, out = self._run("timesheet", "list", "--month", "07/2026")
        self.assertEqual(rc, 2)
        self.assertIn("mês inválido", out)

    def test_add_resolve_cliente_e_projeto(self):
        self._enable()
        cid = tsdb.add_client("Cellera")
        tsdb.add_alias(cid, "celera")
        tsdb.add_project(cid, "403240", "Reforma tributária")
        rc, out = self._add(client="CELERA", project="403240")
        self.assertEqual(rc, 0)
        self.assertNotIn("aviso", out)
        self.assertIn("Cellera", out)  # confirma o nome canônico
        row = tsdb.list_entries(day="2026-07-13")[0]
        self.assertEqual(row["client_id"], cid)
        self.assertEqual(row["project_code"], "403240")

    # -- marcas e filtros do list -----------------------------------------------
    def test_list_marcas_e_filtros(self):
        self._enable()
        self._add(desc="manha")                          # confirmado, não apontado
        rc, _ = self._add(start="14:00", end="17:00", desc="tarde")
        eid = tsdb.list_entries(day="2026-07-13")[-1]["id"]
        tsdb.set_posted([eid], True)                     # tarde: apontado [MD]
        tsdb.upsert_suggestion({
            "work_date": "2026-07-13", "start_time": "18:00", "end_time": "19:00",
            "description": "call sugerida", "meeting_started_at": "2026-07-13T18:00:00"})
        rc, out = self._run("timesheet", "list", "--month", "2026-07")
        self.assertEqual(rc, 0)
        self.assertIn("[MD]", out)
        self.assertIn("[?]", out)
        self.assertIn("sugestões 1:00", out)
        rc, out = self._run("timesheet", "list", "--month", "2026-07", "--suggested")
        self.assertIn("call sugerida", out)
        self.assertNotIn("manha", out)
        rc, out = self._run("timesheet", "list", "--month", "2026-07", "--unposted")
        self.assertIn("manha", out)
        self.assertNotIn("tarde", out)
        self.assertNotIn("call sugerida", out)

    def test_list_mes_vazio(self):
        self._enable()
        rc, out = self._run("timesheet", "list", "--month", "2031-01")
        self.assertEqual(rc, 0)
        self.assertIn("nenhum apontamento em 2031-01", out)

    # -- sync ----------------------------------------------------------------------
    def test_sync_conta_so_novas(self):
        rec = self.tmp / "rec"
        folder = rec / "2026/07/13/14-00"
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(json.dumps({
            "status": "done", "started_at": "2026-07-13T14:00:00",
            "ended_at": "2026-07-13T15:00:00", "title": "Call X", "client": "Delta",
        }), encoding="utf-8")
        self._enable(recordings_dir=rec)
        rc, out = self._run("timesheet", "sync")
        self.assertEqual(rc, 0)
        self.assertIn("1 sugestão(ões) nova(s)", out)
        rc, out = self._run("timesheet", "sync")
        self.assertIn("0 sugestão(ões) nova(s)", out)

    # -- backup ----------------------------------------------------------------------
    def test_backup(self):
        self._enable()
        rc, out = self._run("timesheet", "backup")
        self.assertEqual(rc, 1)
        self.assertIn("ainda não existe", out)
        self._add()
        rc, out = self._run("timesheet", "backup")
        self.assertEqual(rc, 0)
        self.assertIn("backup gravado em", out)
        self.assertEqual(len(list((util.APP_DIR / "backups").glob("timesheet-*.db"))), 1)


if __name__ == "__main__":
    unittest.main()
