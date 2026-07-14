"""Testes do banco do apontamento de horas (épico #118, fundação #119).

Banco DURÁVEL (fonte da verdade) — validamos o contrato que o difere do índice:
migração incremental sem drop, abort em versão futura, dedupe por reunião
(upsert_suggestion), resolução de cliente por alias, totais, backup rotacionado
e a DORMÊNCIA (#126): importar o módulo não cria arquivo; o banco só nasce no
primeiro connect(). Tudo isolado em tempdir — nunca toca o timesheet.db real.

Roda sem dependências externas:  python -m unittest discover -s tests
"""

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import timesheet_db as tsdb  # noqa: E402
from scriba import util  # noqa: E402


class TimesheetDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_ts_"))
        self._app0, self._logs0, self._db0 = util.APP_DIR, util.LOGS_DIR, tsdb.DB_PATH
        util.APP_DIR = self.tmp / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        tsdb.DB_PATH = self.tmp / "timesheet.db"

    def tearDown(self):
        util.APP_DIR, util.LOGS_DIR, tsdb.DB_PATH = self._app0, self._logs0, self._db0
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers --------------------------------------------------------------
    def _entry(self, **kw):
        base = dict(work_date="2026-07-13", start_time="08:00", end_time="13:00")
        base.update(kw)
        return tsdb.add_entry(**base)

    def _suggestion(self, key="2026-07-13T14:00:00", **kw):
        rec = dict(work_date="2026-07-13", start_time="14:00", end_time="15:00",
                   client_text="Coruripe", description="call reforma tributária",
                   meeting_folder="C:/temp/x", meeting_started_at=key)
        rec.update(kw)
        return tsdb.upsert_suggestion(rec)

    # -- dormência (#126) ------------------------------------------------------
    def test_dormencia_criacao_lazy(self):
        # importar o módulo (já feito no topo) não criou nada; nem backup cria
        self.assertFalse(tsdb.DB_PATH.exists())
        self.assertIsNone(tsdb.backup(self.tmp / "bk"))
        self.assertFalse(tsdb.DB_PATH.exists())
        self.assertFalse((self.tmp / "bk").exists())
        # o banco nasce no primeiro connect(), já na versão corrente
        tsdb.connect().close()
        self.assertTrue(tsdb.DB_PATH.exists())
        with sqlite3.connect(str(tsdb.DB_PATH)) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0],
                             tsdb.SCHEMA_VERSION)

    # -- schema / migrações ----------------------------------------------------
    def test_versao_futura_aborta_sem_tocar(self):
        tsdb.connect().close()
        with sqlite3.connect(str(tsdb.DB_PATH)) as c:
            c.execute("PRAGMA user_version = 99")
        with self.assertRaises(RuntimeError):
            tsdb.connect()
        # arquivo intacto: versão preservada, tabelas ainda lá (nada de drop)
        with sqlite3.connect(str(tsdb.DB_PATH)) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 99)
            names = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("entries", names)
        self.assertIn("clients", names)

    def test_migracoes_contiguas(self):
        # toda versão de 1..SCHEMA_VERSION tem migração (caminho incremental completo)
        self.assertEqual(set(tsdb._MIGRATIONS), set(range(1, tsdb.SCHEMA_VERSION + 1)))

    # -- entries: CRUD + validação --------------------------------------------
    def test_add_entry_minutos_derivados_e_validacao(self):
        eid = self._entry()
        row = tsdb.list_entries(day="2026-07-13")[0]
        self.assertEqual(row["id"], eid)
        self.assertEqual(row["minutes"], 300)
        self.assertEqual(row["status"], "confirmed")
        with self.assertRaises(ValueError):
            self._entry(start_time="25:00")
        with self.assertRaises(ValueError):
            self._entry(end_time="08:00")  # fim == início
        with self.assertRaises(ValueError):
            self._entry(work_date="13/07/2026")
        with self.assertRaises(ValueError):
            self._entry(status="pendente")

    def test_update_recalcula_minutos_e_posted(self):
        eid = self._entry()
        self.assertTrue(tsdb.update_entry(eid, end_time="12:00"))
        row = tsdb.list_entries(day="2026-07-13")[0]
        self.assertEqual(row["minutes"], 240)
        # posted mantém posted_at coerente nos dois sentidos
        tsdb.update_entry(eid, posted=True)
        row = tsdb.list_entries(day="2026-07-13")[0]
        self.assertEqual(row["posted"], 1)
        self.assertIsNotNone(row["posted_at"])
        tsdb.update_entry(eid, posted=False)
        row = tsdb.list_entries(day="2026-07-13")[0]
        self.assertEqual(row["posted"], 0)
        self.assertIsNone(row["posted_at"])
        # campo desconhecido é erro de programação; id inexistente é False
        with self.assertRaises(ValueError):
            tsdb.update_entry(eid, foo=1)
        self.assertFalse(tsdb.update_entry(99999, description="x"))

    def test_delete_so_para_manual(self):
        manual = self._entry()
        self.assertTrue(tsdb.delete_entry(manual))
        self.assertFalse(tsdb.delete_entry(manual))  # já foi
        self._suggestion()
        srow = tsdb.list_entries(status="suggested")[0]
        with self.assertRaises(ValueError):
            tsdb.delete_entry(srow["id"])  # de reunião: descarta, não apaga

    # -- clientes / aliases / resolve -----------------------------------------
    def test_resolve_client_nome_e_alias_nocase(self):
        cid = tsdb.add_client("Cellera")
        tsdb.add_alias(cid, "celera farma")
        self.assertEqual(tsdb.resolve_client("cellera"), (cid, "Cellera"))
        self.assertEqual(tsdb.resolve_client("CELLERA"), (cid, "Cellera"))
        self.assertEqual(tsdb.resolve_client("  Celera   Farma "), (cid, "Cellera"))
        self.assertEqual(tsdb.resolve_client("Desconhecida  SA"), (None, "Desconhecida SA"))
        self.assertEqual(tsdb.resolve_client("   "), (None, ""))

    def test_add_client_idempotente_e_alias_conflito(self):
        cid = tsdb.add_client("Abaco")
        self.assertEqual(tsdb.add_client("  ABACO "), cid)  # NOCASE + normalização
        aid = tsdb.add_alias(cid, "abaco studio")
        self.assertEqual(tsdb.add_alias(cid, "Abaco Studio"), aid)  # mesmo dono: no-op
        outro = tsdb.add_client("Delta")
        with self.assertRaises(sqlite3.IntegrityError):
            tsdb.add_alias(outro, "abaco studio")  # alias de outro cliente
        with self.assertRaises(ValueError):
            tsdb.add_client("   ")

    def test_merge_client(self):
        errado = tsdb.add_client("Celera")
        certo = tsdb.add_client("Cellera")
        tsdb.add_alias(errado, "celera sa")
        pid = tsdb.add_project(errado, "403240")
        eid = self._entry(client_id=errado)
        tsdb.merge_client(errado, certo)
        row = tsdb.list_entries(day="2026-07-13")[0]
        self.assertEqual(row["client_id"], certo)
        self.assertEqual(row["client_name"], "Cellera")
        # nome antigo virou alias do destino; alias e projeto re-apontados
        self.assertEqual(tsdb.resolve_client("celera"), (certo, "Cellera"))
        self.assertEqual(tsdb.resolve_client("celera sa"), (certo, "Cellera"))
        self.assertEqual(tsdb.list_projects(certo)[0]["id"], pid)
        self.assertEqual([c["name"] for c in tsdb.list_clients()], ["Cellera"])
        self.assertIsNotNone(eid)

    def test_client_history_recencia_e_dedupe(self):
        """Pré-preenchimento da UI: projetos/descrições por cliente vêm do
        HISTÓRICO (recência), com dedupe NOCASE; cadastrado sem uso entra."""
        self._entry(client_text="Coruripe", project_text="403240",
                    description="analise notas")
        self._entry(start_time="14:00", end_time="15:00", client_text="coruripe",
                    project_text="GAP 1", description="Analise Notas")
        self._entry(work_date="2026-07-14", client_text="Delta",
                    project_text="MM03", status="discarded")
        kid = tsdb.add_client("Kyly")
        tsdb.add_project(kid, "11695")
        hist = tsdb.client_history()
        keys = list(hist)
        self.assertEqual(keys[0].casefold(), "coruripe")           # último usado 1º
        self.assertEqual(hist[keys[0]]["projects"], ["GAP 1", "403240"])  # recência
        self.assertEqual(len(hist[keys[0]]["descriptions"]), 1)    # dedupe NOCASE
        self.assertNotIn("Delta", keys)                            # descartado fora
        self.assertEqual(hist["Kyly"]["projects"], ["11695"])      # cadastrado s/ uso

    def test_known_client_names_uniao_cadastro_e_crus(self):
        tsdb.add_client("Coruripe")
        inativo = tsdb.add_client("Antiga")
        tsdb.set_client_active(inativo, False)
        self._entry(client_text="Delta")                       # cru em uso
        self._entry(start_time="14:00", end_time="15:00",
                    client_text="Gencau", status="discarded")  # descartado: fora
        names = tsdb.known_client_names()
        self.assertIn("Coruripe", names)
        self.assertIn("Delta", names)
        self.assertNotIn("Gencau", names)
        self.assertNotIn("Antiga", names)   # inativo sai dos combos

    def test_rename_e_desativa_cliente(self):
        cid = tsdb.add_client("Celera")
        tsdb.rename_client(cid, "Cellera")
        self.assertEqual(tsdb.resolve_client("cellera"), (cid, "Cellera"))
        with self.assertRaises(ValueError):
            tsdb.rename_client(cid, "  ")
        with self.assertRaises(ValueError):
            tsdb.rename_client(99999, "X")
        tsdb.set_client_active(cid, False)
        self.assertEqual(tsdb.resolve_client("cellera"), (None, "cellera"))  # sai do resolve
        self.assertEqual(tsdb.list_clients(), [])
        self.assertEqual(len(tsdb.list_clients(active_only=False)), 1)
        tsdb.set_client_active(cid, True)  # reativar volta ao normal
        self.assertEqual(tsdb.resolve_client("cellera"), (cid, "Cellera"))

    def test_projects_por_cliente(self):
        a = tsdb.add_client("Coruripe")
        b = tsdb.add_client("Delta")
        p1 = tsdb.add_project(a, "403240", "Reforma tributária")
        self.assertEqual(tsdb.add_project(a, " 403240 "), p1)  # idempotente
        tsdb.add_project(b, "403240")  # mesmo código, outro cliente: ok
        tsdb.add_project(a, "GAP ID 3.4.07-G14")
        self.assertEqual(len(tsdb.list_projects(a)), 2)
        self.assertEqual(len(tsdb.list_projects(b)), 1)

    # -- sugestões: dedupe + regra do reprocesso -------------------------------
    def test_upsert_suggestion_ciclo_completo(self):
        self.assertEqual(self._suggestion(), "created")
        # reprocesso enquanto 'suggested': atualiza (IA corrigiu o cliente)
        self.assertEqual(self._suggestion(client_text="Usina Coruripe"), "updated")
        row = tsdb.list_entries(status="suggested")[0]
        self.assertEqual(row["client_text"], "Usina Coruripe")
        # confirmada: dado do usuário vence a IA — reprocesso não toca
        tsdb.update_entry(row["id"], status="confirmed")
        self.assertEqual(self._suggestion(client_text="Outra"), "skipped")
        self.assertEqual(tsdb.list_entries(day="2026-07-13")[0]["client_text"],
                         "Usina Coruripe")
        # descartada idem (tombstone impede ressurreição)
        outra = "2026-07-13T16:00:00"
        self._suggestion(key=outra)
        sid = tsdb.list_entries(status="suggested")[0]["id"]
        tsdb.update_entry(sid, status="discarded")
        self.assertEqual(self._suggestion(key=outra), "skipped")
        with self.assertRaises(ValueError):
            tsdb.upsert_suggestion({"work_date": "2026-07-13", "start_time": "10:00",
                                    "end_time": "11:00"})  # sem chave de dedupe

    def test_dedupe_no_nivel_do_banco(self):
        key = "2026-07-13T09:00:00"
        self._entry(meeting_started_at=key, origin="scriba")
        with self.assertRaises(sqlite3.IntegrityError):
            self._entry(start_time="14:00", end_time="15:00",
                        meeting_started_at=key, origin="scriba")

    # -- totais ----------------------------------------------------------------
    def test_totais_excluem_suggested_e_discarded(self):
        self._entry()                                            # 300 confirmados
        e2 = self._entry(start_time="14:00", end_time="17:00")   # 180 confirmados
        tsdb.set_posted([e2], True)
        self._suggestion(key="2026-07-13T18:00:00")              # 60 sugeridos
        d = self._entry(work_date="2026-07-14", start_time="08:00", end_time="09:00",
                        status="discarded")
        summary = tsdb.month_summary("2026-07")
        self.assertEqual(summary["total"], 480)
        self.assertEqual(summary["posted"], 180)
        self.assertEqual(summary["unposted"], 300)
        self.assertEqual(summary["suggested"], 60)
        self.assertEqual(tsdb.day_totals("2026-07"), {"2026-07-13": 480})
        self.assertIsNotNone(d)

    def test_set_posted_lote(self):
        ids = [self._entry(), self._entry(start_time="14:00", end_time="17:00")]
        self.assertEqual(tsdb.set_posted(ids, True), 2)
        self.assertEqual(tsdb.set_posted([], True), 0)
        rows = tsdb.list_entries(day="2026-07-13", posted=True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(tsdb.set_posted(ids, False), 2)
        self.assertEqual(tsdb.list_entries(day="2026-07-13", posted=True), [])

    # -- dias especiais ---------------------------------------------------------
    def test_day_notes(self):
        tsdb.set_day_note("2026-07-09", "feriado")
        tsdb.set_day_note("2026-07-09", "FERIADO estadual")  # upsert
        tsdb.set_day_note("2026-07-10", "férias")
        tsdb.set_day_note("2026-07-10", "")                  # vazio remove
        self.assertEqual(tsdb.day_notes_month("2026-07"),
                         {"2026-07-09": "FERIADO estadual"})
        with self.assertRaises(ValueError):
            tsdb.set_day_note("hoje", "feriado")

    # -- backup ------------------------------------------------------------------
    def test_backup_daily_uma_vez_por_dia(self):
        from scriba import config

        state0 = util.STATE_PATH
        util.STATE_PATH = self.tmp / "state.json"
        try:
            ts = config.Timesheet(enabled=True, backup_dir=str(self.tmp / "bk"))
            # dormência: sem banco, não roda nem marca o dia (tenta de novo depois)
            self.assertIsNone(tsdb.backup_daily(ts))
            self.assertNotIn("timesheet_last_backup", util.read_state())
            self._entry()
            out = tsdb.backup_daily(ts)
            self.assertIsNotNone(out)
            self.assertTrue(out.exists())
            self.assertIn("timesheet_last_backup", util.read_state())
            # mesmo dia: em dia, não roda de novo
            self.assertIsNone(tsdb.backup_daily(ts))
            # "ontem" no state: volta a rodar
            util.update_state(timesheet_last_backup="2020-01-01")
            self.assertIsNotNone(tsdb.backup_daily(ts))
        finally:
            util.STATE_PATH = state0

    def test_backup_gera_valido_e_rotaciona(self):
        self._entry()
        dest = self.tmp / "bk"
        out = tsdb.backup(dest, keep=3)
        self.assertIsNotNone(out)
        self.assertTrue(out.exists())
        with sqlite3.connect(str(out)) as c:
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 1)
        # rotação: 3 antigos + o de hoje = 4 -> mantém os 3 mais novos
        for d in ("2020-01-01", "2020-01-02", "2020-01-03"):
            (dest / f"timesheet-{d}.db").write_bytes(b"velho")
        tsdb.backup(dest, keep=3)
        left = sorted(p.name for p in dest.glob("timesheet-*.db"))
        self.assertEqual(len(left), 3)
        self.assertNotIn("timesheet-2020-01-01.db", left)
        self.assertIn(out.name, left)


if __name__ == "__main__":
    unittest.main()
