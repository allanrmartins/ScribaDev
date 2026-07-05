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
        self._app0, self._logs0, self._store0, self._db0 = (
            util.APP_DIR, util.LOGS_DIR, speakers.STORE_PATH, mi.DB_PATH)
        util.APP_DIR = self.tmp / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        speakers.STORE_PATH = util.APP_DIR / "speakers.json"
        mi.DB_PATH = self.tmp / "index.db"
        self.rec = self.tmp / "rec"
        self.rec.mkdir(parents=True)

    def tearDown(self):
        # restaura os globais (DB_PATH volta a None → resolução dinâmica p/ testes seguintes)
        util.APP_DIR, util.LOGS_DIR, speakers.STORE_PATH, mi.DB_PATH = (
            self._app0, self._logs0, self._store0, self._db0)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helper: cria uma pasta de gravação (meta.json + notas.md) -----------
    def _make_meeting(self, name, *, started_at, title="Reunião X", client="",
                      status="done", presentes=None, mencionados=None,
                      body="assunto generico", duration_s=600, meeting_title="",
                      transcript_token="ZZTRANSCONLYZZ", pendencias=None):
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
        # pendencias = lista de bullets crus da seção "## Pendências e Ações" (#76)
        if pendencias:
            lines += ["", "## Pendências e Ações"] + [f"- {p}" for p in pendencias]
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

    def test_transcricao_e_indexada(self):
        # v2/#11: o termo só existe DEPOIS do marcador (na transcrição) e DEVE ser achado
        self._make_meeting("a", started_at="2026-06-10T09:00:00",
                            body="resumo curto", transcript_token="SONATRANSCRICAOZZ")
        mi.index_meeting(self.rec / "a")
        self.assertEqual(len(mi.search(query="SONATRANSCRICAOZZ")), 1)

    def test_include_transcript_escopa_a_busca(self):
        self._make_meeting("a", started_at="2026-06-10T09:00:00",
                            body="ASSUNTODORESUMO", transcript_token="SOTRANSCRICAOQ")
        mi.index_meeting(self.rec / "a")
        # termo só na transcrição: incluído (default) acha; excluído NÃO acha
        self.assertEqual(len(mi.search(query="SOTRANSCRICAOQ", include_transcript=True)), 1)
        self.assertEqual(mi.search(query="SOTRANSCRICAOQ", include_transcript=False), [])
        # termo no resumo: achado mesmo com a transcrição fora
        self.assertEqual(len(mi.search(query="ASSUNTODORESUMO", include_transcript=False)), 1)

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

    def test_transcript_text_pega_so_apos_o_marcador(self):
        md = "## Resumo\nlinha do resumo\n\n## Transcrição completa\n\n**Eu:** fala da call\n"
        self.assertIn("fala da call", mi._transcript_text(md))
        self.assertNotIn("linha do resumo", mi._transcript_text(md))
        self.assertEqual(mi._transcript_text("## Resumo\nsó resumo, sem transcrição"), "")

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

    # -- exclusão pela UI (#16): tombstone .deleted --------------------------
    def test_extract_pula_pasta_com_tombstone(self):
        f = self._make_meeting("a", started_at="2026-06-10T09:00:00", title="Excluida")
        self.assertIsNotNone(mi._extract(f))  # sem tombstone: extrai normal
        (f / mi._DELETED_MARKER).write_text("", encoding="utf-8")
        self.assertIsNone(mi._extract(f))  # com tombstone: ignorada

    def test_reindex_nao_ressuscita_tombstone(self):
        # a nota foi "excluída" mantendo o áudio (tombstone); o reindex NÃO a traz de volta
        f = self._make_meeting("a", started_at="2026-06-10T09:00:00", body="ACHAVELXYZ")
        self._make_meeting("b", started_at="2026-06-11T09:00:00", body="OUTRANOTA")
        self.assertEqual(mi.reindex(self.rec), 2)
        (f / mi._DELETED_MARKER).write_text("", encoding="utf-8")
        self.assertEqual(mi.reindex(self.rec), 1)  # só 'b' é indexada
        self.assertEqual(mi.search(query="ACHAVELXYZ"), [])  # 'a' não ressuscita
        self.assertEqual(len(mi.search(query="OUTRANOTA")), 1)

    def test_mark_deleted_remove_do_indice_e_tombstona(self):
        f = self._make_meeting("a", started_at="2026-06-10T09:00:00", body="ACHAVELXYZ")
        mi.index_meeting(f)
        self.assertEqual(mi.count(), 1)
        mi.mark_deleted(f)
        self.assertEqual(mi.count(), 0)  # sai do índice agora
        self.assertTrue((f / mi._DELETED_MARKER).exists())  # tombstone gravado
        self.assertEqual(mi.reindex(self.rec), 0)  # e segue fora após o reindex
        self.assertEqual(mi.search(query="ACHAVELXYZ"), [])

    def test_index_meeting_limpa_tombstone(self):
        # re-summarize explícito (build_notes → index_meeting) DESFAZ a exclusão
        f = self._make_meeting("a", started_at="2026-06-10T09:00:00", body="ACHAVELXYZ")
        (f / mi._DELETED_MARKER).write_text("", encoding="utf-8")
        self.assertTrue(mi.index_meeting(f))  # (re)indexar remove o tombstone
        self.assertFalse((f / mi._DELETED_MARKER).exists())
        self.assertEqual(len(mi.search(query="ACHAVELXYZ")), 1)

    def test_mark_deleted_sem_pasta_so_remove_do_indice(self):
        f = self._make_meeting("a", started_at="2026-06-10T09:00:00")
        mi.index_meeting(f)
        shutil.rmtree(f)  # pasta já não existe (ex.: apagada à parte)
        mi.mark_deleted(f)  # não cria tombstone solto, não quebra
        self.assertEqual(mi.count(), 0)
        self.assertFalse((f / mi._DELETED_MARKER).exists())

    # -- pendências (action items) no índice (#76) ---------------------------
    def _action_rows(self, folder):
        """Linhas de action_items da pasta, ordenadas por position (helper de teste)."""
        import sqlite3
        with sqlite3.connect(str(mi.DB_PATH)) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(
                "SELECT a.* FROM action_items a JOIN meetings m ON m.id = a.meeting_id "
                "WHERE m.folder = ? ORDER BY a.position", (str(folder),)).fetchall()]

    def test_schema_tem_tabela_action_items(self):
        mi.count()  # cria o schema
        import sqlite3
        with sqlite3.connect(str(mi.DB_PATH)) as c:
            row = c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name='action_items'").fetchone()
        self.assertIsNotNone(row)

    def test_indexa_popula_action_items(self):
        f = self._make_meeting("a", started_at="2026-06-10T09:00:00", pendencias=[
            "**[BLOQUEANTE]** Aplicar nota 123 [00:01:02]",
            "**[ABERTO]** Enviar estimativa",
            "Nada identificado.",  # deve ser ignorado pelo parser
        ])
        mi.index_meeting(f)
        rows = self._action_rows(f)
        self.assertEqual(len(rows), 2)  # o "Nada identificado." não vira linha
        self.assertEqual(rows[0]["label"], "BLOQUEANTE")
        self.assertTrue(rows[0]["text"].startswith("Aplicar nota 123"))
        self.assertEqual([r["position"] for r in rows], [0, 1])
        self.assertEqual([r["state"] for r in rows], ["open", "open"])
        # key bate com o parser (fonte da verdade)
        parsed = notes.parse_action_items((f / "notas.md").read_text(encoding="utf-8"))
        self.assertEqual([r["key"] for r in rows], [p["key"] for p in parsed])

    def test_state_done_vem_do_actions_json(self):
        f = self._make_meeting("a", started_at="2026-06-10T09:00:00", pendencias=[
            "**[BLOQUEANTE]** Aplicar nota 123", "**[ABERTO]** Enviar estimativa"])
        parsed = notes.parse_action_items((f / "notas.md").read_text(encoding="utf-8"))
        notes.set_action_done(f, parsed[0]["key"], True)  # marca o 1º ANTES de indexar
        mi.index_meeting(f)
        rows = self._action_rows(f)
        by_key = {r["key"]: r["state"] for r in rows}
        self.assertEqual(by_key[parsed[0]["key"]], "done")
        self.assertEqual(by_key[parsed[1]["key"]], "open")

    def test_cascata_exclui_action_items(self):
        f = self._make_meeting("a", started_at="2026-06-10T09:00:00",
                               pendencias=["**[ABERTO]** Fazer X"])
        mi.index_meeting(f)
        self.assertEqual(len(self._action_rows(f)), 1)
        mi.remove_meeting(f)
        self.assertEqual(self._action_rows(f), [])  # cascata: nenhuma órfã
        import sqlite3
        with sqlite3.connect(str(mi.DB_PATH)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM action_items").fetchone()[0], 0)

    def test_tombstone_nao_gera_action_items(self):
        f = self._make_meeting("a", started_at="2026-06-10T09:00:00",
                               pendencias=["**[ABERTO]** Fazer X"])
        (f / mi._DELETED_MARKER).write_text("", encoding="utf-8")
        mi.reindex(self.rec)
        self.assertEqual(self._action_rows(f), [])

    def test_set_action_done_reflete_no_indice_sem_reindex(self):
        f = self._make_meeting("a", started_at="2026-06-10T09:00:00", pendencias=[
            "**[BLOQUEANTE]** Aplicar nota 123", "**[ABERTO]** Enviar estimativa"])
        mi.index_meeting(f)
        key = notes.parse_action_items((f / "notas.md").read_text(encoding="utf-8"))[0]["key"]
        notes.set_action_done(f, key, True)  # hook atualiza o índice na hora
        by_key = {r["key"]: r["state"] for r in self._action_rows(f)}
        self.assertEqual(by_key[key], "done")
        notes.set_action_done(f, key, False)  # desmarcar volta p/ open
        by_key = {r["key"]: r["state"] for r in self._action_rows(f)}
        self.assertEqual(by_key[key], "open")

    def test_set_action_state_reflete_dismissed_e_archived(self):
        f = self._make_meeting("a", started_at="2026-06-10T09:00:00", pendencias=[
            "**[BLOQUEANTE]** X1", "**[ABERTO]** X2"])
        mi.index_meeting(f)
        keys = [i["key"] for i in notes.parse_action_items(
            (f / "notas.md").read_text(encoding="utf-8"))]
        notes.set_action_state(f, keys[0], "dismissed")
        notes.set_action_state(f, keys[1], "archived")
        by = {r["key"]: r["state"] for r in self._action_rows(f)}
        self.assertEqual((by[keys[0]], by[keys[1]]), ("dismissed", "archived"))
        # contagens e filtros do índice enxergam os estados novos (nenhum 'open' sobra)
        self.assertEqual(mi.action_counts(), {"dismissed": 1, "archived": 1})
        self.assertEqual([i["text"] for i in mi.list_action_items(state="dismissed")], ["X1"])

    def test_set_action_state_resiliente_a_indice_indisponivel(self):
        # índice quebrado NÃO pode derrubar o set_action_done nem perder o .actions.json
        f = self._make_meeting("a", started_at="2026-06-10T09:00:00",
                               pendencias=["**[ABERTO]** Fazer X"])
        mi.index_meeting(f)
        key = notes.parse_action_items((f / "notas.md").read_text(encoding="utf-8"))[0]["key"]
        orig = mi._connect
        mi._connect = lambda: (_ for _ in ()).throw(RuntimeError("índice fora"))
        try:
            self.assertFalse(mi.set_action_state(f, key, "done"))  # não levanta, devolve False
            notes.set_action_done(f, key, True)  # também não levanta
        finally:
            mi._connect = orig
        self.assertTrue(notes.load_action_state(f).get(key))  # .actions.json íntegro

    def test_set_action_state_folder_nao_indexada(self):
        # pasta sem linha no índice: UPDATE não casa nada → False, sem erro
        self.assertFalse(mi.set_action_state(self.rec / "inexistente", "abc123", "done"))

    def test_action_counts_por_estado_e_recorte(self):
        a = self._make_meeting("a", started_at="2026-06-10T09:00:00", pendencias=[
            "**[BLOQUEANTE]** X1", "**[ABERTO]** X2"])
        b = self._make_meeting("b", started_at="2026-06-20T09:00:00",
                               pendencias=["**[ABERTO]** Y1"])
        mi.index_meeting(a)
        mi.index_meeting(b)
        # marca X1 como done
        k = notes.parse_action_items((a / "notas.md").read_text(encoding="utf-8"))[0]["key"]
        notes.set_action_done(a, k, True)
        self.assertEqual(mi.action_counts(), {"open": 2, "done": 1})
        # recorte temporal: só a reunião 'b' (>= 15/06) → 1 open, 0 done
        self.assertEqual(mi.action_counts(since="2026-06-15"), {"open": 1})

    def test_list_action_items_filtros_e_contexto(self):
        a = self._make_meeting("a", started_at="2026-06-10T09:00:00", client="ACME",
                               title="Kickoff", pendencias=[
                                   "**[BLOQUEANTE]** X1", "**[ABERTO]** X2"])
        b = self._make_meeting("b", started_at="2026-06-20T09:00:00", client="Globex",
                               pendencias=["**[ABERTO]** Y1"])
        mi.index_meeting(a)
        mi.index_meeting(b)
        # todos: 3 itens, mais recente (b) primeiro; itens de 'a' na ordem da nota
        alli = mi.list_action_items()
        self.assertEqual([i["text"] for i in alli], ["Y1", "X1", "X2"])
        # contexto p/ abrir a nota
        self.assertEqual(alli[0]["client"], "Globex")
        self.assertEqual(alli[0]["folder"], str(b))
        self.assertEqual(alli[1]["title"], "Kickoff")
        # filtro por cliente (parcial, case-insensitive)
        self.assertEqual([i["text"] for i in mi.list_action_items(client="acme")], ["X1", "X2"])
        # filtro por estado
        k = notes.parse_action_items((a / "notas.md").read_text(encoding="utf-8"))[0]["key"]
        notes.set_action_done(a, k, True)
        self.assertEqual([i["text"] for i in mi.list_action_items(state="done")], ["X1"])
        self.assertEqual({i["text"] for i in mi.list_action_items(state="open")}, {"X2", "Y1"})
        # recorte temporal
        self.assertEqual([i["text"] for i in mi.list_action_items(since="2026-06-15")], ["Y1"])

    def test_list_action_items_paridade_com_parse(self):
        # paridade com o cálculo antigo (open_action_items): mesmos itens abertos
        a = self._make_meeting("a", started_at="2026-06-10T09:00:00", pendencias=[
            "**[BLOQUEANTE]** Aplicar nota", "**[ABERTO]** Enviar estimativa"])
        mi.index_meeting(a)
        idx_open = {i["key"] for i in mi.list_action_items(state="open")}
        parse_open = {i["key"] for i in notes.parse_action_items(
            (a / "notas.md").read_text(encoding="utf-8"))}  # nenhum resolvido ainda
        self.assertEqual(idx_open, parse_open)


class IndexPathIsolationTests(unittest.TestCase):
    """Rede de segurança (#84): com DB_PATH None (default), o índice é RESOLVIDO de
    util.APP_DIR — isolar só o APP_DIR já redireciona, então nenhum teste toca o
    index.db REAL do usuário por esquecer de setar mi.DB_PATH."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_idxiso_"))
        self._app0, self._logs0, self._db0 = util.APP_DIR, util.LOGS_DIR, mi.DB_PATH
        util.APP_DIR = self.tmp / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        mi.DB_PATH = None   # SEM override explícito: deve resolver de util.APP_DIR

    def tearDown(self):
        util.APP_DIR, util.LOGS_DIR, mi.DB_PATH = self._app0, self._logs0, self._db0
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_db_path_resolve_de_app_dir(self):
        self.assertEqual(mi._db_path(), util.APP_DIR / "index.db")

    def test_index_writer_grava_sob_app_dir_isolado(self):
        rec = self.tmp / "rec" / "m"
        rec.mkdir(parents=True)
        (rec / "meta.json").write_text(
            '{"status":"done","started_at":"2026-06-10T09:00:00"}', encoding="utf-8")
        (rec / "notas.md").write_text("# X\n\n## Resumo\nok\n", encoding="utf-8")
        self.assertTrue(mi.index_meeting(rec))
        self.assertEqual(mi.count(), 1)
        # o índice nasceu SOB o APP_DIR isolado (tmp), nunca no path real
        db = util.APP_DIR / "index.db"
        self.assertTrue(db.exists())
        self.assertTrue(str(db).startswith(str(self.tmp)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
