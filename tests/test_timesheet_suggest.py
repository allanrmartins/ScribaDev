"""Testes do motor de sugestões do timesheet (épico #118, #120).

Validamos: arredondamento (round_hhmm), a função pura suggestion_from_meta
(elegibilidade, arredondamento, meia-noite clampada, cliente cru), o fluxo
suggest_for_folder (resolve cliente, idempotência, regra do reprocesso, nunca
levanta) e a varredura sync_pending. Dormência (#126): sem config explícita e
com enabled=false, o motor se auto-bloqueia sem criar banco.

Roda sem dependências externas:  python -m unittest discover -s tests
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import config, util  # noqa: E402
from scriba import timesheet_db as tsdb  # noqa: E402
from scriba import timesheet_suggest as tss  # noqa: E402

_CFG = config.Timesheet(enabled=True, round_minutes=15, min_meeting_minutes=10)


def _meta(*, status="done", started="2026-07-13T14:03:12", ended="2026-07-13T15:09:40",
          duration=None, title="Reforma tributária - notas outbound", client="Vetra"):
    m = {"status": status, "started_at": started, "ended_at": ended,
         "title": title, "client": client}
    if duration is not None:
        m["duration_seconds"] = duration
    return m


class RoundHhmmTests(unittest.TestCase):
    def test_modos_e_virada_de_hora(self):
        self.assertEqual(tss.round_hhmm("14:07", 15), "14:00")   # nearest p/ baixo
        self.assertEqual(tss.round_hhmm("14:08", 15), "14:15")   # nearest p/ cima
        self.assertEqual(tss.round_hhmm("16:53", 15), "17:00")   # vira a hora
        self.assertEqual(tss.round_hhmm("14:07", 15, "down"), "14:00")
        self.assertEqual(tss.round_hhmm("14:01", 15, "up"), "14:15")
        self.assertEqual(tss.round_hhmm("14:00", 15, "up"), "14:00")  # exato não sobe

    def test_step_zero_e_clamp(self):
        self.assertEqual(tss.round_hhmm("14:07", 0), "14:07")    # 0 = identidade
        self.assertEqual(tss.round_hhmm("23:58", 15), "23:59")   # nunca 24:00
        self.assertEqual(tss.round_hhmm("23:50", 15, "up"), "23:59")

    def test_mode_invalido(self):
        with self.assertRaises(ValueError):
            tss.round_hhmm("14:00", 15, "floor")


class SuggestionFromMetaTests(unittest.TestCase):
    """Função PURA — sem banco, sem filesystem."""

    def test_done_completo(self):
        rec = tss.suggestion_from_meta(_meta(), _CFG)
        self.assertEqual(rec["work_date"], "2026-07-13")
        self.assertEqual(rec["start_time"], "14:00")   # 14:03 -> nearest 15
        self.assertEqual(rec["end_time"], "15:15")     # 15:09 -> nearest 15
        self.assertEqual(rec["client_text"], "Vetra")
        self.assertEqual(rec["description"], "Reforma tributária - notas outbound")
        self.assertEqual(rec["meeting_started_at"], "2026-07-13T14:03:12")

    def test_nao_elegiveis_viram_none(self):
        for status in ("failed", "discarded", "too_short", "recording", "transcribed"):
            self.assertIsNone(tss.suggestion_from_meta(_meta(status=status), _CFG))
        self.assertIsNone(tss.suggestion_from_meta({}, _CFG))
        self.assertIsNone(tss.suggestion_from_meta(_meta(ended=None), _CFG))
        self.assertIsNone(tss.suggestion_from_meta(_meta(started="ontem"), _CFG))
        # curta demais: 5 min de duração real < min_meeting_minutes=10
        self.assertIsNone(tss.suggestion_from_meta(
            _meta(ended="2026-07-13T14:08:12", duration=300), _CFG))
        # fim antes do início (meta corrompido)
        self.assertIsNone(tss.suggestion_from_meta(
            _meta(ended="2026-07-13T13:00:00"), _CFG))

    def test_meia_noite_clampa_e_sinaliza(self):
        rec = tss.suggestion_from_meta(
            _meta(started="2026-07-13T23:30:00", ended="2026-07-14T00:40:00"), _CFG)
        self.assertEqual(rec["work_date"], "2026-07-13")
        self.assertEqual(rec["end_time"], "23:59")
        self.assertIn("meia-noite", rec["description"])

    def test_arredondamento_colapsado_garante_um_passo(self):
        cfg = config.Timesheet(enabled=True, round_minutes=15, min_meeting_minutes=0)
        rec = tss.suggestion_from_meta(
            _meta(started="2026-07-13T14:02:00", ended="2026-07-13T14:05:00",
                  duration=180), cfg)
        self.assertEqual((rec["start_time"], rec["end_time"]), ("14:00", "14:15"))

    def test_cliente_e_titulo_vazios(self):
        rec = tss.suggestion_from_meta(_meta(client=None, title=None), _CFG)
        self.assertEqual(rec["client_text"], "")
        self.assertEqual(rec["description"], "")

    def test_duration_seconds_vence_o_delta(self):
        # delta de 66 min, mas o gravado diz 4 min: não vira sugestão
        self.assertIsNone(tss.suggestion_from_meta(_meta(duration=240), _CFG))


class SuggestFlowTests(unittest.TestCase):
    """suggest_for_folder + sync_pending, com banco e pastas fake isolados."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_tss_"))
        self._app0, self._logs0, self._cfg0, self._db0 = (
            util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH, tsdb.DB_PATH)
        util.APP_DIR = self.tmp / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        util.CONFIG_PATH = util.APP_DIR / "config.toml"
        tsdb.DB_PATH = self.tmp / "timesheet.db"
        self.rec = self.tmp / "rec"
        self.rec.mkdir(parents=True)

    def tearDown(self):
        util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH, tsdb.DB_PATH = (
            self._app0, self._logs0, self._cfg0, self._db0)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _folder(self, name="2026/07/13/14-03_Reuniao", **kw):
        folder = self.rec / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text(
            json.dumps(_meta(**kw), ensure_ascii=False), encoding="utf-8")
        return folder

    def test_cria_resolve_cliente_e_e_idempotente(self):
        cid = tsdb.add_client("Usina Vetra")
        tsdb.add_alias(cid, "vetra")
        folder = self._folder()
        self.assertEqual(tss.suggest_for_folder(folder, _CFG), "created")
        row = tsdb.list_entries(status="suggested")[0]
        self.assertEqual(row["client_id"], cid)          # alias resolveu
        self.assertEqual(row["client_text"], "")         # canônico via client_id
        self.assertEqual(row["origin"], "scriba")
        self.assertEqual(row["meeting_folder"], str(folder))
        # segunda chamada: atualiza a mesma linha, não duplica
        self.assertIn(tss.suggest_for_folder(folder, _CFG), ("updated", "skipped"))
        self.assertEqual(len(tsdb.list_entries(status="suggested")), 1)

    def test_cliente_nao_resolvido_preserva_texto_cru(self):
        folder = self._folder(client="Cliente Novo SA")
        tss.suggest_for_folder(folder, _CFG)
        row = tsdb.list_entries(status="suggested")[0]
        self.assertIsNone(row["client_id"])
        self.assertEqual(row["client_text"], "Cliente Novo SA")

    def test_reprocesso_atualiza_suggested_mas_nao_confirmed(self):
        folder = self._folder()
        tss.suggest_for_folder(folder, _CFG)
        # re-summarize corrigiu o cliente no meta
        (folder / "meta.json").write_text(
            json.dumps(_meta(client="Delta Peças"), ensure_ascii=False), encoding="utf-8")
        self.assertEqual(tss.suggest_for_folder(folder, _CFG), "updated")
        row = tsdb.list_entries(status="suggested")[0]
        self.assertEqual(row["client_text"], "Delta Peças")
        # confirmada: dado do usuário vence a IA
        tsdb.update_entry(row["id"], status="confirmed")
        (folder / "meta.json").write_text(
            json.dumps(_meta(client="Outra"), ensure_ascii=False), encoding="utf-8")
        self.assertEqual(tss.suggest_for_folder(folder, _CFG), "skipped")
        self.assertEqual(tsdb.list_entries()[0]["client_text"], "Delta Peças")

    def test_nunca_levanta(self):
        self.assertEqual(tss.suggest_for_folder(self.tmp / "nao-existe", _CFG), "ignored")
        folder = self.rec / "corrompida"
        folder.mkdir()
        (folder / "meta.json").write_text("{lixo", encoding="utf-8")
        self.assertEqual(tss.suggest_for_folder(folder, _CFG), "ignored")

    def test_sync_pending_varre_e_conta_so_criadas(self):
        self._folder("a/14-00", started="2026-07-13T14:00:00", ended="2026-07-13T15:00:00")
        self._folder("b/16-00", started="2026-07-13T16:00:00", ended="2026-07-13T17:00:00")
        self._folder("c/18-00", started="2026-07-13T18:00:00", ended="2026-07-13T19:00:00",
                     status="failed")
        self._folder("d/20-00", started="2026-07-13T20:00:00", ended="2026-07-13T20:05:00",
                     duration=300)  # curta demais
        self.assertEqual(tss.sync_pending(self.rec, _CFG), 2)
        self.assertEqual(len(tsdb.list_entries(status="suggested")), 2)
        self.assertEqual(tss.sync_pending(self.rec, _CFG), 0)  # idempotente

    def test_activate_e_deactivate_ciclo(self):
        """#126: rotina única de ativação - enabled + banco + varredura inicial;
        desativar preserva os dados; reativar reencontra tudo."""
        import dataclasses

        self._folder()
        cfg = config.load()
        config.save(dataclasses.replace(cfg, output=dataclasses.replace(
            cfg.output, recordings_dir=str(self.rec))))
        # ativa: enabled persistido, banco criado, reunião fake vira sugestão
        self.assertEqual(tss.activate(), 1)
        self.assertTrue(config.load().timesheet.enabled)
        self.assertTrue(tsdb._db_path().exists())
        self.assertEqual(len(tsdb.list_entries(status="suggested")), 1)
        # idempotente: reativar não duplica nada
        self.assertEqual(tss.activate(), 0)
        self.assertEqual(len(tsdb.list_entries(status="suggested")), 1)
        # desativa: gate volta a segurar, banco INTACTO
        tss.deactivate()
        self.assertFalse(config.load().timesheet.enabled)
        self.assertTrue(tsdb._db_path().exists())
        nova = self._folder("outra/15-00", started="2026-07-13T15:00:00",
                            ended="2026-07-13T16:00:00")
        self.assertEqual(tss.suggest_for_folder(nova), "ignored")  # dormente de novo
        # reativa: dados antigos preservados + a reunião nova entra
        self.assertEqual(tss.activate(), 1)
        self.assertEqual(len(tsdb.list_entries(status="suggested")), 2)

    def test_dormencia_auto_bloqueia_sem_cfg_explicita(self):
        # config real (isolado) com enabled=false: motor não roda nem cria banco
        folder = self._folder()
        self.assertEqual(tss.suggest_for_folder(folder), "ignored")
        self.assertEqual(tss.sync_pending(self.rec), 0)
        self.assertFalse(tsdb.DB_PATH.exists())
        # ativado no config: passa a funcionar sem cfg explícita
        import dataclasses
        cfg = config.load()
        config.save(dataclasses.replace(
            cfg, timesheet=dataclasses.replace(cfg.timesheet, enabled=True)))
        self.assertEqual(tss.suggest_for_folder(folder), "created")
        self.assertTrue(tsdb.DB_PATH.exists())


if __name__ == "__main__":
    unittest.main()
