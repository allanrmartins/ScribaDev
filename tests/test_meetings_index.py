"""Testes do índice de busca das reuniões (#10): scriba.meetings_index.

Índice SQLite+FTS5 DERIVADO das pastas — aqui validamos extração, busca
(full-text/participante/cliente/data/status), idempotência, reconstrução,
remoção e os hooks (relabel re-indexa). Tudo isolado em tempdir: DB próprio,
APP_DIR/store de voz redirecionados — nunca toca o index.db/ speakers.json reais.

Roda sem dependências externas:  python -m unittest discover -s tests
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import meetings_index as mi  # noqa: E402
from scriba import notes, speakers, util  # noqa: E402


class MeetingsIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_idx_"))
        # isolamento total: índice e store de voz em tempdir
        util.APP_DIR = self.tmp / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        speakers.STORE_PATH = util.APP_DIR / "speakers.json"
        mi.DB_PATH = self.tmp / "index.db"
        self.rec = self.tmp / "rec"
        self.rec.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helper: cria uma pasta de gravação (meta.json + notas.md) -----------
    def _make_meeting(self, name, *, started_at, title="Reunião X", client="",
                      status="done", presentes=None, mencionados=None,
                      body="assunto generico", duration_s=600, meeting_title="",
                      transcript_token="ZZTRANSCONLYZZ"):
        folder = self.rec / name
        folder.mkdir(parents=True, exist_ok=True)
        meta = {
            "status": status, "started_at": started_at, "ended_at": started_at,
            "duration_seconds": duration_s, "title": title, "client": client,
            "export_path": str(folder / "nota.md"), "meeting_title": meeting_title,
        }
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        lines = [f"# {title}", "", "## Resumo", body, "", "## Participantes", "", "**Presentes:**"]
        for n, d in (presentes or {}).items():
            lines.append(f"- **{n}** — {d}")
        if mencionados:
            lines += ["", "**Mencionados (não necessariamente presentes):**"]
            lines += [f"- **{n}** — citado" for n in mencionados]
        # token SÓ na transcrição: prova que a transcrição completa NÃO é indexada
        lines += ["", mi._TRANSCRIPT_MARKER, "", f"**[00:00:00] Eu:** {transcript_token}", ""]
        (folder / "notas.md").write_text("\n".join(lines), encoding="utf-8")
        return folder

    # -- schema --------------------------------------------------------------
    def test_schema_criado_e_versionado(self):
        self.assertEqual(mi.count(), 0)  # cria o schema na 1ª conexão
        self.assertTrue(mi.DB_PATH.exists())
        import sqlite3
        with sqlite3.connect(str(mi.DB_PATH)) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], mi.SCHEMA_VERSION)

    # -- indexação + busca full-text ----------------------------------------
    def test_indexa_e_busca_fulltext(self):
        self._make_meeting("a", started_at="2026-06-10T09:00:00",
                            title="Kickoff", body="discutimos o ASSUNTOUNICOXYZ a fundo")
        self.assertTrue(mi.index_meeting(self.rec / "a"))
        self.assertEqual(mi.count(), 1)
        # acha por termo do corpo do resumo
        self.assertEqual(len(mi.search(query="ASSUNTOUNICOXYZ")), 1)
        # acha por termo do título
        self.assertEqual(len(mi.search(query="Kickoff")), 1)
        # termo inexistente → vazio
        self.assertEqual(mi.search(query="INEXISTENTEQWE"), [])

    def test_transcricao_nao_e_indexada(self):
        self._make_meeting("a", started_at="2026-06-10T09:00:00",
                            body="resumo curto", transcript_token="SONATRANSCRICAOZZ")
        mi.index_meeting(self.rec / "a")
        self.assertEqual(mi.search(query="SONATRANSCRICAOZZ"), [])  # está só após o marcador

    # -- filtros -------------------------------------------------------------
    def test_busca_por_participante(self):
        self._make_meeting("a", started_at="2026-06-10T09:00:00",
                            presentes={"Eu": "você", "Marcão": "lidera"},
                            mencionados=["Renato"])
        mi.index_meeting(self.rec / "a")
        self.assertEqual(len(mi.search(participant="Marc")), 1)   # casa parcial
        self.assertEqual(mi.search(participant="Fulano"), [])
        # mencionado também entra na tabela de participantes (kind diferente)
        res = mi.search(participant="Renato")
        self.assertEqual(len(res), 1)
        kinds = {p["name"]: p["kind"] for p in res[0]["participants"]}
        self.assertEqual(kinds.get("Renato"), "mentioned")
        self.assertEqual(kinds.get("Marcão"), "present")

    def test_busca_por_cliente(self):
        self._make_meeting("a", started_at="2026-06-10T09:00:00", client="Acme Corp")
        self._make_meeting("b", started_at="2026-06-11T09:00:00", client="Globex")
        mi.index_meeting(self.rec / "a")
        mi.index_meeting(self.rec / "b")
        self.assertEqual(len(mi.search(client="acme")), 1)  # case-insensitive parcial
        self.assertEqual(len(mi.search()), 2)

    def test_busca_por_intervalo_de_data(self):
        for i, day in enumerate(("05", "10", "20")):
            self._make_meeting(f"m{i}", started_at=f"2026-06-{day}T14:30:00")
            mi.index_meeting(self.rec / f"m{i}")
        # since/until inclusivos por dia (até "2026-06-10" pega a reunião das 14:30)
        res = mi.search(since="2026-06-06", until="2026-06-10")
        self.assertEqual([r["started_at"][:10] for r in res], ["2026-06-10"])
        # ordenação: mais recente primeiro
        all_dates = [r["started_at"][:10] for r in mi.search()]
        self.assertEqual(all_dates, ["2026-06-20", "2026-06-10", "2026-06-05"])

    def test_busca_por_status(self):
        self._make_meeting("done", started_at="2026-06-10T09:00:00", status="done")
        self._make_meeting("fail", started_at="2026-06-11T09:00:00", status="failed")
        mi.index_meeting(self.rec / "done")
        mi.index_meeting(self.rec / "fail")
        self.assertEqual(len(mi.search(status="done")), 1)
        self.assertEqual(len(mi.search(status=None)), 2)  # todos

    def test_query_com_caracteres_especiais_nao_quebra(self):
        self._make_meeting("a", started_at="2026-06-10T09:00:00", body="usamos C++ no projeto")
        mi.index_meeting(self.rec / "a")
        # hífen, aspas e operadores do FTS não podem virar erro de sintaxe
        for q in ("foo-bar", 'as "aspas"', "OR AND NOT", "c++", "a*b:c"):
            self.assertIsInstance(mi.search(query=q), list)

    # -- idempotência / reconstrução / remoção -------------------------------
    def test_reindex_idempotente_sem_duplicar(self):
        f = self._make_meeting("a", started_at="2026-06-10T09:00:00", title="V1")
        mi.index_meeting(f)
        # muda o título e reindexa a MESMA pasta → continua 1 linha, conteúdo novo
        self._make_meeting("a", started_at="2026-06-10T09:00:00", title="V2 ATUALIZADO")
        mi.index_meeting(f)
        self.assertEqual(mi.count(), 1)
        self.assertEqual(len(mi.search(query="ATUALIZADO")), 1)
        self.assertEqual(mi.search(query="V1"), [])  # FTS antigo foi substituído

    def test_reindex_varre_pastas_e_descarta_drift(self):
        self._make_meeting("a", started_at="2026-06-10T09:00:00")
        self._make_meeting("b", started_at="2026-06-11T09:00:00")
        self.assertEqual(mi.reindex(self.rec), 2)
        self.assertEqual(mi.count(), 2)
        # pasta apagada à mão (drift) some na próxima reconstrução
        shutil.rmtree(self.rec / "b")
        self.assertEqual(mi.reindex(self.rec), 1)
        self.assertEqual(mi.count(), 1)

    def test_remove_meeting(self):
        f = self._make_meeting("a", started_at="2026-06-10T09:00:00",
                               presentes={"Eu": "você"})
        mi.index_meeting(f)
        self.assertTrue(mi.remove_meeting(f))
        self.assertEqual(mi.count(), 0)
        self.assertEqual(mi.search(query="Reunião"), [])
        self.assertFalse(mi.remove_meeting(f))  # já não existe

    # -- extração ------------------------------------------------------------
    def test_extract_sem_meta_e_none(self):
        (self.rec / "vazia").mkdir()
        self.assertIsNone(mi._extract(self.rec / "vazia"))
        self.assertFalse(mi.index_meeting(self.rec / "vazia"))

    def test_summary_body_corta_na_transcricao(self):
        md = "## Resumo\nlinha do resumo\n\n## Transcrição completa\n\n**Eu:** fala da call\n"
        self.assertIn("resumo", mi._summary_body(md))
        self.assertNotIn("fala da call", mi._summary_body(md))

    # -- versionamento de schema (reconstruível) -----------------------------
    def test_schema_divergente_recria(self):
        self._make_meeting("a", started_at="2026-06-10T09:00:00")
        mi.index_meeting(self.rec / "a")
        self.assertEqual(mi.count(), 1)
        import sqlite3
        with sqlite3.connect(str(mi.DB_PATH)) as c:
            c.execute("PRAGMA user_version = 999")  # finge schema futuro
        # próxima conexão detecta divergência, dropa e recria vazio (sem crash)
        self.assertEqual(mi.count(), 0)
        with sqlite3.connect(str(mi.DB_PATH)) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], mi.SCHEMA_VERSION)

    # -- hook: relabel_speakers re-indexa (#1 + #10) -------------------------
    def test_relabel_dispara_reindex(self):
        f = self._make_meeting("relabel", started_at="2026-06-10T20:00:00",
                               title="Daily", presentes={"Eu": "você", "Participante 2": "fala muito"})
        (f / "voices.json").write_text(json.dumps(
            {"Participante 2": {"embedding": [0.0, 1.0, 0.0], "auto": False}}), encoding="utf-8")
        (f / "transcript.json").write_text(json.dumps(
            [{"start": 0.0, "end": 1.0, "speaker": "Participante 2", "text": "oi"}]), encoding="utf-8")
        mi.index_meeting(f)
        self.assertTrue(mi.search(participant="Participante 2"))
        # relabel reescreve notas.md e DEVE re-indexar (hook em notes.relabel_speakers)
        self.assertTrue(notes.relabel_speakers(f, {"Participante 2": "Marcelo"}))
        self.assertTrue(mi.search(participant="Marcelo"))
        self.assertFalse(mi.search(participant="Participante 2"))

    # -- reindex_if_needed: gatilho do boot (#12) ----------------------------
    def test_reindex_if_needed_popula_quando_vazio(self):
        self._make_meeting("a", started_at="2026-06-10T09:00:00")
        self._make_meeting("b", started_at="2026-06-11T09:00:00")
        self.assertFalse(mi.DB_PATH.exists())  # índice ainda nem existe
        self.assertEqual(mi.reindex_if_needed(self.rec), 2)
        self.assertEqual(mi.count(), 2)

    def test_reindex_if_needed_e_noop_quando_populado(self):
        self._make_meeting("a", started_at="2026-06-10T09:00:00")
        mi.index_meeting(self.rec / "a")
        # nova pasta no disco NÃO indexada: se reindex_if_needed varresse, viraria 2
        self._make_meeting("b", started_at="2026-06-11T09:00:00")
        self.assertEqual(mi.reindex_if_needed(self.rec), 1)  # já populado → no-op
        self.assertEqual(mi.count(), 1)
        self.assertEqual(len(mi.search(status="done")), 1)  # pasta 'b' NÃO foi varrida

    def test_reindex_if_needed_reconstrui_apos_schema_divergente(self):
        self._make_meeting("a", started_at="2026-06-10T09:00:00")
        mi.index_meeting(self.rec / "a")
        import sqlite3
        with sqlite3.connect(str(mi.DB_PATH)) as c:
            c.execute("PRAGMA user_version = 999")  # finge schema futuro → será zerado
        # _ensure_schema zera no connect; reindex_if_needed vê 0 e repopula do disco
        self.assertEqual(mi.reindex_if_needed(self.rec), 1)
        self.assertEqual(mi.count(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
