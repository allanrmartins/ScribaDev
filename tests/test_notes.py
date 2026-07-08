"""Testes de scriba.notes.split_header — tolerância a preâmbulo do modelo (issue #18).

Roda sem dependências externas:  python -m unittest discover -s tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import meetings_index as mi  # noqa: E402
from scriba import notes, speakers, util  # noqa: E402
from scriba.notes import split_header  # noqa: E402


class ScanMeetingsByStatusTests(unittest.TestCase):
    """A capa lê as reuniões EM ANDAMENTO das pastas (o índice só as ganha prontas)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_scan_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, name, status, **extra):
        d = self.tmp / name
        d.mkdir(parents=True)
        (d / "meta.json").write_text(
            json.dumps({"status": status, **extra}, ensure_ascii=False), encoding="utf-8")
        return d

    def test_filtra_por_status_e_anexa_folder(self):
        self._mk("2026/07/06/09-31", "summarizing", started_at="2026-07-06T09:31:00")
        self._mk("2026/07/06/10-24", "diarizing", started_at="2026-07-06T10:24:00")
        self._mk("2026/07/05/08-00", "done", started_at="2026-07-05T08:00:00")
        got = notes.scan_meetings_by_status(
            self.tmp, ("recorded", "transcribing", "diarizing", "transcribed", "summarizing"))
        self.assertEqual(len(got), 2)                       # só as em andamento
        by_status = {m["status"] for m in got}
        self.assertEqual(by_status, {"summarizing", "diarizing"})
        self.assertTrue(all("folder" in m for m in got))    # caminho da pasta anexado

    def test_meta_ilegivel_e_pulado_sem_levantar(self):
        d = self.tmp / "quebrada"
        d.mkdir()
        (d / "meta.json").write_text("{ nao é json", encoding="utf-8")
        self._mk("ok", "transcribing")
        got = notes.scan_meetings_by_status(self.tmp, ("transcribing",))
        self.assertEqual(len(got), 1)

    def test_dir_inexistente_devolve_vazio(self):
        self.assertEqual(notes.scan_meetings_by_status(self.tmp / "nao-existe", ("done",)), [])


class UpdateNoteMetaTests(unittest.TestCase):
    """Editar título/cliente sincroniza meta.json + notas.md + .md exportado + o ÍNDICE
    (de onde a capa e a busca por cliente leem). Índice isolado num tmp (não polui o real)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_upd_"))
        self._app0, self._logs0, self._db0 = util.APP_DIR, util.LOGS_DIR, mi.DB_PATH
        util.APP_DIR = self.tmp / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        mi.DB_PATH = self.tmp / "index.db"
        self.rec = self.tmp / "rec"
        self.rec.mkdir(parents=True)

    def tearDown(self):
        import shutil
        util.APP_DIR, util.LOGS_DIR, mi.DB_PATH = self._app0, self._logs0, self._db0
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make(self, client=""):
        folder = self.rec / "2026" / "07" / "06" / "10-24"
        folder.mkdir(parents=True)
        export = self.tmp / "2026-07-06_10-24_reuniao.md"
        meta_line = f"*2026-07-06 10:24 · 5 min{(' · Cliente: ' + client) if client else ''}*"
        md = (f"---\ntitulo: Alinhamento\ndata: 2026-07-06T10:24:00\ncliente: {client}\n---\n\n"
              f"# Alinhamento\n\n{meta_line}\n\n## Resumo\ncorpo da reunião\n")
        (folder / "notas.md").write_text(md, encoding="utf-8")
        export.write_text(md, encoding="utf-8")
        meta = {"status": "done", "started_at": "2026-07-06T10:24:00",
                "title": "Alinhamento", "client": client, "export_path": str(export)}
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return folder, export

    def test_cliente_sincroniza_meta_notas_export_e_indice(self):
        folder, export = self._make(client="")
        self.assertTrue(notes.update_note_meta(folder, client="abaco"))
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["client"], "abaco")                     # FONTE do índice
        self.assertIn("Cliente: abaco", (folder / "notas.md").read_text(encoding="utf-8"))
        self.assertIn("Cliente: abaco", export.read_text(encoding="utf-8"))
        hits = mi.search(client="abaco")                              # capa/busca refletem
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["client"], "abaco")

    def test_remover_cliente_e_manter_titulo(self):
        folder, _ = self._make(client="acme")
        self.assertTrue(notes.update_note_meta(folder, client=""))    # title=None: não mexe
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["client"], "")
        self.assertEqual(meta["title"], "Alinhamento")               # título intacto
        self.assertEqual(mi.search(client="acme"), [])               # saiu da busca

    def test_pasta_inexistente_devolve_false(self):
        self.assertFalse(notes.update_note_meta(self.rec / "nao-existe", client="x"))

    def test_extra_target_sincroniza_md_exibido_com_export_obsoleto(self):
        # regressão #92: export_path aponta p/ um .md que não existe mais (pasta de
        # export trocada / arquivo movido); o .md realmente EXIBIDO na lista é outro.
        # Sem extra_targets, ele não era tocado e o "✓ Salvo" mentia.
        folder, _ = self._make(client="")
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        meta["export_path"] = str(self.tmp / "sumiu" / "antigo.md")   # obsoleto: não existe
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        exibido = self.tmp / "novo-local" / "2026-07-06_10-24_reuniao.md"
        exibido.parent.mkdir()
        exibido.write_text((folder / "notas.md").read_text(encoding="utf-8"), encoding="utf-8")

        ok = notes.update_note_meta(folder, title="Renomeada", client="abaco",
                                    extra_targets=[exibido])
        self.assertTrue(ok)
        conteudo = exibido.read_text(encoding="utf-8")
        self.assertIn("# Renomeada", conteudo)          # o arquivo EXIBIDO reflete
        self.assertIn("Cliente: abaco", conteudo)

    def test_uma_escrita_por_alvo_quando_exibido_e_o_export(self):
        # caso comum: o .md exibido É o export_path -> dedup evita ler/reescrever 2x
        folder, export = self._make(client="")
        writes: list[str] = []
        orig = util.atomic_write_text

        def _spy(p, data):
            writes.append(str(p))
            return orig(p, data)

        with mock.patch.object(util, "atomic_write_text", _spy):
            notes.update_note_meta(folder, title="X", client="acme", extra_targets=[export])
        # export aparece 1x só (não duplicado), e cada .md foi escrito 1x (título+cliente juntos)
        self.assertEqual(writes.count(str(export)), 1)
        self.assertEqual(writes.count(str(folder / "notas.md")), 1)

    def test_titulo_e_cliente_numa_passada(self):
        folder, export = self._make(client="")
        self.assertTrue(notes.update_note_meta(folder, title="Novo Título", client="acme"))
        for md in (folder / "notas.md", export):
            txt = md.read_text(encoding="utf-8")
            self.assertIn("# Novo Título", txt)
            self.assertIn("titulo: Novo Título", txt)
            self.assertIn("Cliente: acme", txt)


class SplitHeaderTests(unittest.TestCase):
    def test_caso_feliz(self):
        body, title, client = split_header("TITULO: Migração SAP\nCLIENTE: Acme\n\n## Resumo\nlinha")
        self.assertEqual(title, "Migração SAP")
        self.assertEqual(client, "Acme")
        self.assertEqual(body, "## Resumo\nlinha")
        self.assertNotIn("TITULO:", body)

    def test_preambulo_em_branco(self):
        body, title, client = split_header("\n\nTITULO: Foo\nCLIENTE: Bar\n\ncorpo")
        self.assertEqual((title, client), ("Foo", "Bar"))
        self.assertEqual(body, "corpo")

    def test_cerca_de_codigo(self):
        body, title, client = split_header("```markdown\nTITULO: Foo\nCLIENTE: Bar\n\ncorpo aqui")
        self.assertEqual((title, client), ("Foo", "Bar"))
        self.assertEqual(body, "corpo aqui")
        self.assertNotIn("```", body)

    def test_preambulo_conversacional(self):
        body, title, client = split_header("Aqui está o resumo:\nTITULO: Foo\nCLIENTE: Bar\ncorpo")
        self.assertEqual((title, client), ("Foo", "Bar"))
        self.assertEqual(body, "corpo")

    def test_negativo_titulo_no_meio_do_corpo(self):
        # conteúdo real antes de um TITULO: que é, na verdade, citação da transcrição
        original = "## Discussão\nFulano disse:\nTITULO: isto é fala, não header\nmais corpo"
        body, title, client = split_header(original)
        self.assertIsNone(title)
        self.assertIsNone(client)
        self.assertEqual(body, original)  # texto intacto, header do meio NÃO consumido

    def test_negativo_cliente_apos_texto_real(self):
        original = "Resumo real começa aqui\nCLIENTE: tarde demais"
        body, title, client = split_header(original)
        self.assertIsNone(title)
        self.assertIsNone(client)
        self.assertEqual(body, original)

    def test_cliente_interrogacao_vira_none(self):
        body, title, client = split_header("TITULO: Foo\nCLIENTE: ?\ncorpo")
        self.assertEqual(title, "Foo")
        self.assertIsNone(client)
        self.assertEqual(body, "corpo")

    def test_so_titulo_sem_cliente(self):
        body, title, client = split_header("TITULO: Só título\n## Resumo\ncorpo")
        self.assertEqual(title, "Só título")
        self.assertIsNone(client)
        self.assertEqual(body, "## Resumo\ncorpo")
        self.assertNotIn("TITULO:", body)

    def test_ordem_invertida(self):
        # robustez extra: modelo emite CLIENTE antes de TITULO
        body, title, client = split_header("CLIENTE: Bar\nTITULO: Foo\ncorpo")
        self.assertEqual((title, client), ("Foo", "Bar"))
        self.assertEqual(body, "corpo")

    def test_sem_header_nenhum(self):
        original = "## Apenas um resumo comum\nsem header algum"
        self.assertEqual(split_header(original), (original, None, None))

    def test_titulo_com_aspas(self):
        _, title, _ = split_header('TITULO: "Entre aspas"\nCLIENTE: Acme\ncorpo')
        self.assertEqual(title, "Entre aspas")


class RelabelSpeakersTests(unittest.TestCase):
    """notes.relabel_speakers (#1): aprende a voz E corrige a nota já gerada."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="scriba_relabel_"))
        # store de voz E índice ISOLADOS — relabel_speakers re-indexa (hook index_meeting),
        # então SEM isolar mi.DB_PATH o teste gravaria no index.db REAL do usuário.
        self._app0, self._logs0, self._store0, self._db0 = (
            util.APP_DIR, util.LOGS_DIR, speakers.STORE_PATH, mi.DB_PATH)
        util.APP_DIR = self.d / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        speakers.STORE_PATH = util.APP_DIR / "speakers.json"
        mi.DB_PATH = util.APP_DIR / "index.db"
        self.rec = self.d / "rec"
        self.rec.mkdir(parents=True)
        self.export = self.d / "2026-06-10_20-00_reuniao.md"
        (self.rec / "voices.json").write_text(json.dumps({
            "Participante 1": {"embedding": [1.0, 0.0, 0.0], "auto": False, "score": 0.0},
            "Participante 2": {"embedding": [0.0, 1.0, 0.0], "auto": False, "score": 0.0},
        }), encoding="utf-8")
        (self.rec / "transcript.json").write_text(json.dumps([
            {"start": 0.0, "end": 1.0, "speaker": "Eu", "text": "oi"},
            {"start": 1.0, "end": 2.0, "speaker": "Participante 2", "text": "aqui e o Marcelo"},
        ]), encoding="utf-8")
        nota = ("# Reuniao\n\n**[00:00:01] Participante 2:** aqui e o Marcelo\n\n"
                "## Participantes\n- Participante 2 (voz)\n")
        (self.rec / "notas.md").write_text(nota, encoding="utf-8")
        self.export.write_text(nota, encoding="utf-8")
        (self.rec / "meta.json").write_text(
            json.dumps({"export_path": str(self.export)}), encoding="utf-8")

    def tearDown(self):
        util.APP_DIR, util.LOGS_DIR, speakers.STORE_PATH, mi.DB_PATH = (
            self._app0, self._logs0, self._store0, self._db0)

    def test_aprende_e_corrige_nota_e_transcricao(self):
        ok = notes.relabel_speakers(self.rec, {"Participante 2": "Marcelo"})
        self.assertTrue(ok)
        # transcript.json: campo speaker trocado
        turns = json.loads((self.rec / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(turns[1]["speaker"], "Marcelo")
        # markdown local E exportado: trocado, sem sobra de "Participante 2"
        for md in (self.rec / "notas.md", self.export):
            txt = md.read_text(encoding="utf-8")
            self.assertIn("Marcelo:", txt)
            self.assertNotIn("Participante 2", txt)
        # store global: aprendeu Marcelo com o embedding da voz 2
        st = speakers.load_store()
        self.assertEqual([e["name"] for e in st], ["Marcelo"])
        self.assertEqual(st[0]["embedding"], [0.0, 1.0, 0.0])
        # voices.json: chave renomeada e marcada como rotulada à mão
        voices = json.loads((self.rec / "voices.json").read_text(encoding="utf-8"))
        self.assertIn("Marcelo", voices)
        self.assertNotIn("Participante 2", voices)
        self.assertTrue(voices["Marcelo"]["labeled"])

    def test_sem_mudanca_e_noop(self):
        self.assertFalse(notes.relabel_speakers(self.rec, {"Participante 1": ""}))
        self.assertFalse(notes.relabel_speakers(self.rec, {"Participante 1": "Participante 1"}))
        self.assertEqual(speakers.load_store(), [])

    def test_word_boundary_nao_pega_participante_12(self):
        (self.rec / "notas.md").write_text("Participante 1 e Participante 12 aqui", encoding="utf-8")
        notes.relabel_speakers(self.rec, {"Participante 1": "Ana"})
        txt = (self.rec / "notas.md").read_text(encoding="utf-8")
        self.assertIn("Ana e Participante 12 aqui", txt)


class _FakeOutput:
    def __init__(self, new_dir: Path, rec_dir: Path, export_dir: str = ""):
        self.export_dir = export_dir
        self._new, self._rec = new_dir, rec_dir

    def resolved_export_dir(self) -> Path:
        return Path(self.export_dir) if self.export_dir else self._new

    def resolved_recordings_dir(self) -> Path:
        return self._rec


class _FakeCfg:
    def __init__(self, output):
        self.output = output


class MigrateExportDirTests(unittest.TestCase):
    """notes.migrate_export_dir: tira as notas do antigo default (Documentos/OneDrive)
    e leva p/ a pasta local, reescrevendo export_path dos meta.json. One-time."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="scriba_migr_"))
        self.docs = self.d / "Documents"          # "Documentos" (no OneDrive na vida real)
        self.old = self.docs / "ScribaDev"
        self.old.mkdir(parents=True)
        self.new = self.d / "local" / "Notas"     # novo default LOCAL
        self.rec = self.d / "rec"
        self.rec.mkdir()
        # 2 notas no antigo default
        (self.old / "2026-06-10_19-01_reuniao.md").write_text("# A", encoding="utf-8")
        (self.old / "2026-07-08_09-30_reuniao.md").write_text("# B", encoding="utf-8")
        # uma gravação cujo meta aponta p/ a nota antiga
        folder = self.rec / "2026" / "07" / "08" / "09-30"
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(json.dumps(
            {"export_path": str(self.old / "2026-07-08_09-30_reuniao.md")}), encoding="utf-8")
        self.meta_path = folder / "meta.json"
        self._state0 = util.STATE_PATH
        util.STATE_PATH = self.d / "state.json"

    def tearDown(self):
        util.STATE_PATH = self._state0

    def _run(self, export_dir: str = ""):
        cfg = _FakeCfg(_FakeOutput(self.new, self.rec, export_dir))
        with mock.patch("scriba.notes.load", return_value=cfg), \
             mock.patch("scriba.util.documents_dir", return_value=self.docs), \
             mock.patch("scriba.meetings_index.reindex") as reindex:
            n = notes.migrate_export_dir()
        return n, reindex

    def test_move_reescreve_meta_e_reindexa(self):
        n, reindex = self._run()
        self.assertEqual(n, 2)
        self.assertTrue((self.new / "2026-06-10_19-01_reuniao.md").exists())
        self.assertTrue((self.new / "2026-07-08_09-30_reuniao.md").exists())
        self.assertEqual(list(self.old.glob("*.md")), [])          # esvaziou o antigo
        meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["export_path"], str(self.new / "2026-07-08_09-30_reuniao.md"))
        reindex.assert_called_once()
        self.assertTrue(util.read_state().get("export_migrated_v1"))

    def test_idempotente(self):
        self._run()
        n2, reindex2 = self._run()                                  # 2ª vez: nada a fazer
        self.assertEqual(n2, 0)
        reindex2.assert_not_called()

    def test_colisao_arquiva_duplicata_e_esvazia_onedrive(self):
        scriba_old = self.docs / "Scriba"                          # pasta pré-fork
        scriba_old.mkdir()
        # nome que JÁ existe em ScribaDev (colisão) + um exclusivo do Scriba
        (scriba_old / "2026-06-10_19-01_reuniao.md").write_text("# dup", encoding="utf-8")
        (scriba_old / "2020-01-01_08-00_reuniao.md").write_text("# so scriba", encoding="utf-8")
        n, _ = self._run()
        self.assertEqual(n, 3)                                     # 2 do ScribaDev + 1 exclusivo
        self.assertTrue((self.new / "2026-06-10_19-01_reuniao.md").exists())   # primária
        self.assertTrue((self.new / "2020-01-01_08-00_reuniao.md").exists())   # exclusiva migrou
        arch = list((self.new / "_duplicados_migrados").glob("*.md"))
        self.assertEqual(len(arch), 1)                            # a duplicata foi arquivada
        self.assertEqual(list(self.old.glob("*.md")), [])         # nada sobrou no OneDrive
        self.assertEqual(list(scriba_old.glob("*.md")), [])

    def test_respeita_export_dir_manual(self):
        custom = self.d / "escolha_do_usuario"
        n, reindex = self._run(export_dir=str(custom))
        self.assertEqual(n, 0)
        reindex.assert_not_called()
        self.assertEqual(len(list(self.old.glob("*.md"))), 2)       # não mexeu nas notas
        self.assertTrue(util.read_state().get("export_migrated_v1"))


class RemoveSpeakersTests(unittest.TestCase):
    """notes.remove_speakers (voz fantasma): tira a voz do voices.json E o participante
    da seção Presentes da nota (some do painel e do contador da capa)."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="scriba_rmspk_"))
        # index isolado: remove_speakers re-indexa → sem isolar mi.DB_PATH gravaria no real.
        self._app0, self._logs0, self._db0 = (util.APP_DIR, util.LOGS_DIR, mi.DB_PATH)
        util.APP_DIR = self.d / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        mi.DB_PATH = util.APP_DIR / "index.db"
        self.rec = self.d / "rec"
        self.rec.mkdir(parents=True)
        self.export = self.d / "2026-06-10_20-00_reuniao.md"
        (self.rec / "voices.json").write_text(json.dumps({
            "Richard": {"embedding": [1.0], "auto": True},
            "Participante 2": {"embedding": [0.0, 1.0], "auto": False},
            "Participante 3": {"embedding": [0.0, 0.0, 1.0], "auto": False},
        }), encoding="utf-8")
        nota = ("# Reunião\n\n## Participantes\n\n### Presentes\n"
                "- **Eu** — desenvolvedor\n"
                "- **Richard** — cliente\n"
                "- **Participante 2** — voz não identificada\n"
                "- **Participante 3** — voz não identificada\n\n"
                "## Transcrição completa\n**[00:00] Participante 2:** oi\n")
        (self.rec / "notas.md").write_text(nota, encoding="utf-8")
        self.export.write_text(nota, encoding="utf-8")
        (self.rec / "meta.json").write_text(
            json.dumps({"export_path": str(self.export)}), encoding="utf-8")

    def tearDown(self):
        util.APP_DIR, util.LOGS_DIR, mi.DB_PATH = (self._app0, self._logs0, self._db0)

    def test_remove_tira_do_voices_e_da_nota(self):
        ok = notes.remove_speakers(self.rec, {"Participante 2", "Participante 3"})
        self.assertTrue(ok)
        voices = json.loads((self.rec / "voices.json").read_text(encoding="utf-8"))
        self.assertEqual(set(voices), {"Richard"})
        for md in (self.rec / "notas.md", self.export):
            pres, _ = notes.parse_participants(md.read_text(encoding="utf-8"))
            self.assertEqual(set(pres), {"Eu", "Richard"})
        # a transcrição NÃO é tocada (backup de rastreabilidade intacto)
        self.assertIn("Participante 2:", (self.rec / "notas.md").read_text(encoding="utf-8"))

    def test_remove_nome_embutido_no_rotulo(self):
        (self.rec / "notas.md").write_text(
            "# R\n\n## Participantes\n\n### Presentes\n"
            "- **Participante 2 (Fulano)** — voz\n- **Richard** — cliente\n", encoding="utf-8")
        notes.remove_speakers(self.rec, {"Participante 2"})
        pres, _ = notes.parse_participants((self.rec / "notas.md").read_text(encoding="utf-8"))
        self.assertEqual(set(pres), {"Richard"})

    def test_remove_vazio_e_desconhecido_e_noop(self):
        self.assertFalse(notes.remove_speakers(self.rec, set()))
        self.assertFalse(notes.remove_speakers(self.rec, {"Participante 99"}))
        voices = json.loads((self.rec / "voices.json").read_text(encoding="utf-8"))
        self.assertEqual(len(voices), 3)


class ParseParticipantsTests(unittest.TestCase):
    """notes.parse_participants: extrai a seção Participantes do resumo."""

    _MD = (
        "# Reunião X\n\n## Objetivo\nbla\n\n## Participantes\n\n"
        "**Presentes:**\n"
        "- **Eu** — desenvolvedor ABAP\n"
        "- **Participante 1** — conduz a reunião; nome não estabelecido com segurança\n"
        "- **Participante 2** — Alex (identificado: responde quando chamam 'Alex')\n\n"
        "**Mencionados (não necessariamente presentes):**\n"
        "- **Renato** — cliente\n"
        "- **Ebert / Herbert** — interno\n\n"
        "## Transcrição completa\n\n**[00:00:00] Eu:** oi\n"
    )

    def test_presentes_e_mencionados(self):
        pres, menc = notes.parse_participants(self._MD)
        self.assertIn("Eu", pres)
        self.assertTrue(pres["Participante 2"].startswith("Alex ("))
        self.assertEqual(menc, ["Renato", "Ebert / Herbert"])
        self.assertNotIn("Renato", pres)

    def test_sem_secao_participantes(self):
        self.assertEqual(notes.parse_participants("# Nota\n\n## Resumo\ntexto"), ({}, []))

    # formato real da IA: "## Participantes" com sub-cabeçalhos "### Presentes"/"### Mencionados"
    _MD_H3 = (
        "# Reunião\n\n## Participantes\n\n"
        "### Presentes\n"
        "- **Eu** — desenvolvedor\n"
        "- **Participante 1 (Guilherme Lima)** — gerente; conduziu [00:28]\n"
        "- **Participante 2** — voz não identificada\n"
        "- **Participante 4 (Carlos)** — diretor\n\n"
        "### Mencionados (não confirmados como vozes)\n"
        "- **Célio** — supervisor\n\n"
        "## Transcrição completa\n**[00:00] Eu:** oi\n"
    )

    def test_subcabecalho_h3_nao_encerra_secao(self):
        # regressão: "### Presentes" começa com "## " e fazia o parser parar antes de ler ninguém
        pres, menc = notes.parse_participants(self._MD_H3)
        self.assertIn("Participante 1", pres)
        self.assertIn("Participante 4", pres)
        self.assertEqual(menc, ["Célio"])  # bullets sob "### Mencionados"

    def test_nome_embutido_no_rotulo(self):
        pres, _ = notes.parse_participants(self._MD_H3)
        self.assertNotIn("Participante 1 (Guilherme Lima)", pres)  # chave normalizada p/ "Participante 1"
        self.assertEqual(notes.guess_voice_name("Participante 1", pres["Participante 1"]), "Guilherme Lima")
        self.assertEqual(notes.guess_voice_name("Participante 4", pres["Participante 4"]), "Carlos")


class GuessVoiceNameTests(unittest.TestCase):
    """notes.guess_voice_name: palpite de nome a partir da descrição da IA."""

    def test_ja_nomeada_retorna_o_proprio_label(self):
        self.assertEqual(notes.guess_voice_name("Pedro", "papel técnico"), "Pedro")

    def test_nome_no_inicio_entre_parenteses(self):
        self.assertEqual(notes.guess_voice_name("Participante 2", "Alex (identificado: …)"), "Alex")

    def test_nome_composto_no_inicio(self):
        # nome com sobrenome (vindo do rótulo "Participante 1 (Guilherme Lima)")
        self.assertEqual(
            notes.guess_voice_name("Participante 1", "Guilherme Lima (gerente de projeto)"), "Guilherme Lima")

    def test_marcador_possivelmente(self):
        self.assertEqual(
            notes.guess_voice_name("Participante 5", "papel técnico; possivelmente Paulo, mas citado em 3a pessoa"),
            "Paulo")

    def test_sem_palpite_vira_vazio(self):
        self.assertEqual(
            notes.guess_voice_name("Participante 1", "conduz a reunião; nome não estabelecido com segurança"), "")

    def test_nome_entre_aspas(self):
        self.assertEqual(
            notes.guess_voice_name("Participante 1", 'coordena a call; possivelmente "Suzy" (chamada pelo nome)'),
            "Suzy")

    def test_nome_entre_aspas_curvas(self):
        self.assertEqual(
            notes.guess_voice_name("Participante 1", "coordena; possivelmente “Suzy” (chamada)"), "Suzy")

    def test_nome_com_barra_pega_o_primeiro(self):
        self.assertEqual(
            notes.guess_voice_name("Participante 3", 'analista; possivelmente "Marcão/Marco" (chamado e responde)'),
            "Marcão")

    def test_marcador_com_artigo(self):
        self.assertEqual(
            notes.guess_voice_name("Participante 2", "conduz; provavelmente o 'Pedro' citado"), "Pedro")


class DateMaskTests(unittest.TestCase):
    """util.format_date_br + date_br_to_iso: máscara e validação dos filtros de data."""

    def test_format_acumulando_digitos(self):
        # formata corretamente em qualquer ponto da digitação
        self.assertEqual(util.format_date_br("1"), "1")
        self.assertEqual(util.format_date_br("19"), "19")
        self.assertEqual(util.format_date_br("190"), "19/0")
        self.assertEqual(util.format_date_br("1902"), "19/02")
        self.assertEqual(util.format_date_br("190219"), "19/02/19")
        self.assertEqual(util.format_date_br("19021988"), "19/02/1988")

    def test_format_descarta_nao_digito_e_limita_8(self):
        self.assertEqual(util.format_date_br("aqsas"), "")
        self.assertEqual(util.format_date_br("19/02/19x88"), "19/02/1988")
        self.assertEqual(util.format_date_br("1902198899"), "19/02/1988")  # cap em 8

    def test_iso_de_data_valida(self):
        self.assertEqual(util.date_br_to_iso("19/02/1988"), "1988-02-19")
        self.assertEqual(util.date_br_to_iso(" 01/12/2026 "), "2026-12-01")

    def test_iso_parcial_ou_invalida_vira_vazio(self):
        for s in ("", "19/02", "31/02/2020", "99/99/9999", "19/21/8890", "abc", "1988-02-19"):
            self.assertEqual(util.date_br_to_iso(s), "", s)


class TimeMaskTests(unittest.TestCase):
    """util.format_time_hhmm + time_hhmm_ok: máscara e validação do filtro de hora."""

    def test_format_acumulando(self):
        self.assertEqual(util.format_time_hhmm("0"), "0")
        self.assertEqual(util.format_time_hhmm("09"), "09")
        self.assertEqual(util.format_time_hhmm("093"), "09:3")
        self.assertEqual(util.format_time_hhmm("0930"), "09:30")
        self.assertEqual(util.format_time_hhmm("09:30"), "09:30")

    def test_format_descarta_nao_digito_e_limita_4(self):
        self.assertEqual(util.format_time_hhmm("asasas"), "")
        self.assertEqual(util.format_time_hhmm("09h30"), "09:30")
        self.assertEqual(util.format_time_hhmm("093099"), "09:30")  # cap em 4

    def test_validade(self):
        for s in ("00:00", "09:30", "23:59"):
            self.assertTrue(util.time_hhmm_ok(s), s)
        for s in ("", "9", "24:00", "09:60", "99:99", "ab:cd"):
            self.assertFalse(util.time_hhmm_ok(s), s)


class DateRangeFilterTests(unittest.TestCase):
    """util.date_range_filter: só DE = aquele dia; DE+ATÉ = intervalo (ordem livre)."""

    def test_so_de_e_aquele_dia(self):
        self.assertEqual(util.date_range_filter("10/06/2026", ""), ("2026-06-10", "2026-06-10"))

    def test_so_ate_e_aquele_dia(self):
        self.assertEqual(util.date_range_filter("", "10/06/2026"), ("2026-06-10", "2026-06-10"))

    def test_de_e_ate_viram_intervalo(self):
        self.assertEqual(util.date_range_filter("10/06/2026", "12/06/2026"),
                         ("2026-06-10", "2026-06-12"))

    def test_ordem_invertida_ajusta_min_max(self):
        self.assertEqual(util.date_range_filter("12/06/2026", "10/06/2026"),
                         ("2026-06-10", "2026-06-12"))

    def test_vazio_ou_invalido_nao_filtra(self):
        self.assertEqual(util.date_range_filter("", ""), (None, None))
        self.assertEqual(util.date_range_filter("19/02", "abc"), (None, None))

    def test_um_valido_outro_parcial_usa_o_valido_como_dia(self):
        self.assertEqual(util.date_range_filter("10/06/2026", "12/06"), ("2026-06-10", "2026-06-10"))
        self.assertEqual(util.date_range_filter("xx", "12/06/2026"), ("2026-06-12", "2026-06-12"))


class ActionItemsTests(unittest.TestCase):
    """notes.parse_action_items / action_item_key: seção 'Pendências e Ações' (#22)."""

    _MD = (
        "# Reunião\n\n## Decisões\n- decidiu X\n\n"
        "## Pendências e Ações\n"
        "- **[BLOQUEANTE — Eu]** Liberar a transport request [00:00:09] [00:29:25]\n"
        "- **[ABERTO]** Confirmar GRC ou DRC [00:08:33]\n"
        "- **Indefinido:** prazo do go live não foi dado\n"
        "Este parágrafo não é um item e deve ser ignorado.\n\n"
        "## Participantes\n- **Eu** — dev\n"
    )

    def test_extrai_bullets_com_label_e_texto(self):
        items = notes.parse_action_items(self._MD)
        self.assertEqual(len(items), 3)  # 2 com label + 1 "Indefinido"; ignora o parágrafo
        self.assertEqual(items[0]["label"], "BLOQUEANTE — Eu")
        self.assertTrue(items[0]["text"].startswith("Liberar"))
        self.assertEqual(items[2]["label"], "")  # "Indefinido:" não tem **[...]** no começo

    def test_ignora_nada_identificado(self):
        self.assertEqual(notes.parse_action_items("## Pendências e Ações\n- Nada identificado.\n"), [])

    def test_sem_secao(self):
        self.assertEqual(notes.parse_action_items("# X\n\n## Resumo\ntexto"), [])

    def test_nao_vaza_proxima_secao(self):
        items = notes.parse_action_items(self._MD)
        self.assertNotIn("dev", " ".join(i["text"] for i in items))  # "## Participantes" não entra

    def test_negrito_envolvendo_rotulo_e_frase(self):
        # variação real da IA (2026-07-08): o ** fecha DEPOIS da frase, não após o rótulo —
        # e o texto exibido não pode vazar asteriscos (capa/hub renderizam texto puro)
        md = ("## Pendências e Ações\n"
              '- **["Eu"] Aplicar as 6 notas no DEV hoje à tarde** e avisar no grupo. [00:15:43]\n'
              "- **[Wisney / Guilherme Lima] Solicitar a Célio Soares** que envie "
              "**ainda na tarde** a documentação. [00:33:58]\n")
        items = notes.parse_action_items(md)
        self.assertEqual(items[0]["label"], "Eu")                    # aspas da IA removidas
        self.assertEqual(items[1]["label"], "Wisney / Guilherme Lima")
        for it in items:
            self.assertNotIn("**", it["text"])
        self.assertTrue(items[0]["text"].startswith("Aplicar as 6 notas"))
        self.assertIn("Solicitar a Célio Soares que envie ainda na tarde", items[1]["text"])

    def test_key_estavel_apos_tolerancia_de_negrito(self):
        # a key vem do raw (intacto): estados salvos no .actions.json não podem invalidar
        raw = '**["Eu"] Aplicar as 6 notas no DEV** e avisar. [00:15:43]'
        items = notes.parse_action_items(f"## Pendências e Ações\n- {raw}\n")
        self.assertEqual(items[0]["key"], notes.action_item_key(raw))

    def test_key_ignora_timestamp(self):
        self.assertEqual(notes.action_item_key("faz X [00:01:02]"), notes.action_item_key("faz X [12:34:56]"))

    def test_key_difere_por_texto(self):
        self.assertNotEqual(notes.action_item_key("faz X"), notes.action_item_key("faz Y"))


class ActionStateTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        # set_action_done reflete no índice (#76): isolar APP_DIR/DB_PATH p/ não tocar o real
        self._app0, self._logs0, self._db0 = util.APP_DIR, util.LOGS_DIR, mi.DB_PATH
        util.APP_DIR = self.tmp / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        mi.DB_PATH = util.APP_DIR / "index.db"

    def tearDown(self):
        util.APP_DIR, util.LOGS_DIR, mi.DB_PATH = self._app0, self._logs0, self._db0
        self._td.cleanup()

    def test_marca_e_desmarca(self):
        notes.set_action_done(self.tmp, "abc", True)
        self.assertEqual(notes.load_action_state(self.tmp), {"abc": "done"})  # estado nomeado (#77)
        notes.set_action_done(self.tmp, "abc", False)
        self.assertEqual(notes.load_action_state(self.tmp), {})

    def test_load_ausente_vira_vazio(self):
        self.assertEqual(notes.load_action_state(self.tmp / "nao_existe"), {})

    # -- estados nomeados + retrocompat (#77) --------------------------------
    def test_retrocompat_le_bool_legado_como_done(self):
        # arquivo antigo {key: true} deve ser lido como 'done' (sem migração destrutiva)
        (self.tmp / ".actions.json").write_text(
            json.dumps({"k1": True, "k2": False}), encoding="utf-8")
        self.assertEqual(notes.load_action_state(self.tmp), {"k1": "done"})  # false = aberto (omitido)

    def test_le_estados_nomeados(self):
        (self.tmp / ".actions.json").write_text(
            json.dumps({"a": "done", "b": "dismissed", "c": "archived", "d": "open", "e": "bogus"}),
            encoding="utf-8")
        # 'open' e valor desconhecido saem do dict (ausência = aberto)
        self.assertEqual(notes.load_action_state(self.tmp),
                         {"a": "done", "b": "dismissed", "c": "archived"})

    def test_set_action_state_open_remove_a_chave(self):
        notes.set_action_state(self.tmp, "k", "dismissed")
        self.assertEqual(notes.load_action_state(self.tmp), {"k": "dismissed"})
        notes.set_action_state(self.tmp, "k", "open")  # volta p/ aberto = remove
        self.assertEqual(notes.load_action_state(self.tmp), {})
        # e o arquivo não acumula "open"
        raw = json.loads((self.tmp / ".actions.json").read_text(encoding="utf-8"))
        self.assertNotIn("k", raw)

    def test_set_action_state_dismissed_e_archived(self):
        notes.set_action_state(self.tmp, "k1", "dismissed")
        notes.set_action_state(self.tmp, "k2", "archived")
        self.assertEqual(notes.load_action_state(self.tmp), {"k1": "dismissed", "k2": "archived"})

    def test_set_action_state_valor_invalido_vira_open(self):
        notes.set_action_state(self.tmp, "k", "xpto")  # estado inválido → open (remove)
        self.assertEqual(notes.load_action_state(self.tmp), {})


class OpenActionItemsTests(unittest.TestCase):
    """notes.open_action_items: agrega os itens ABERTOS de várias reuniões p/ a capa (#56)."""

    _MD = (
        "# Reunião\n\n## Pendências e Ações\n"
        "- **[BLOQUEANTE]** Aplicar nota 123 no QAS\n"
        "- **[ABERTO]** Enviar estimativa\n"
    )

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        # set_action_done reflete no índice (#76): isolar APP_DIR/DB_PATH p/ não tocar o real
        self._app0, self._logs0, self._db0 = util.APP_DIR, util.LOGS_DIR, mi.DB_PATH
        util.APP_DIR = self.tmp / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        mi.DB_PATH = util.APP_DIR / "index.db"

    def tearDown(self):
        util.APP_DIR, util.LOGS_DIR, mi.DB_PATH = self._app0, self._logs0, self._db0
        self._td.cleanup()

    def _meeting(self, name, md=None):
        folder = self.tmp / name
        folder.mkdir()
        note = folder / f"{name}.md"
        note.write_text(self._MD if md is None else md, encoding="utf-8")
        return {"export_path": str(note), "folder": str(folder), "title": name, "client": "ACME"}

    def test_agrega_itens_abertos_com_contexto(self):
        m = self._meeting("reuniao_a")
        items = notes.open_action_items([m])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "reuniao_a")
        self.assertEqual(items[0]["client"], "ACME")
        self.assertEqual(items[0]["note_path"], m["export_path"])
        self.assertEqual(items[0]["folder"], m["folder"])  # #80: folder p/ marcar/dispensar
        self.assertTrue(items[0]["text"].startswith("Aplicar"))

    def test_dismissed_e_archived_saem_do_contador(self):
        m = self._meeting("reuniao_x")
        keys = [i["key"] for i in notes.open_action_items([m])]
        notes.set_action_state(Path(m["folder"]), keys[0], "dismissed")
        # só o segundo (open) sobra; dismissed não conta como ativa
        rest = notes.open_action_items([m])
        self.assertEqual([i["key"] for i in rest], [keys[1]])
        notes.set_action_state(Path(m["folder"]), keys[1], "archived")
        self.assertEqual(notes.open_action_items([m]), [])  # archived também sai

    def test_descarta_resolvidos(self):
        m = self._meeting("reuniao_b")
        first = notes.open_action_items([m])[0]
        notes.set_action_done(Path(m["folder"]), first["key"], True)   # resolve o 1º
        items = notes.open_action_items([m])
        self.assertEqual(len(items), 1)                                 # sobra só o aberto
        self.assertNotEqual(items[0]["key"], first["key"])

    def test_preserva_ordem_das_reunioes(self):
        a, b = self._meeting("rec_a"), self._meeting("rec_b")
        items = notes.open_action_items([a, b])
        self.assertEqual([i["title"] for i in items], ["rec_a", "rec_a", "rec_b", "rec_b"])

    def test_ignora_sem_export_ou_folder(self):
        self.assertEqual(notes.open_action_items([{"export_path": "", "folder": "x"}]), [])
        self.assertEqual(notes.open_action_items([{"export_path": "x", "folder": ""}]), [])

    def test_ignora_md_inexistente(self):
        bogus = {"export_path": str(self.tmp / "nao_existe.md"), "folder": str(self.tmp)}
        self.assertEqual(notes.open_action_items([bogus]), [])

    def test_reuniao_sem_pendencias(self):
        m = self._meeting("vazia", md="# X\n\n## Resumo\ntexto\n")
        self.assertEqual(notes.open_action_items([m]), [])


class ArchiveOldActionItemsTests(unittest.TestCase):
    """notes.archive_old_action_items: arquiva em massa o backlog antigo (#77)."""

    _MD = (
        "# Reunião\n\n## Pendências e Ações\n"
        "- **[BLOQUEANTE]** Aplicar nota 123 no QAS\n"
        "- **[ABERTO]** Enviar estimativa\n"
    )

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        # set_action_state reflete no índice: isolar APP_DIR/DB_PATH p/ não tocar o real
        self._app0, self._logs0, self._db0 = util.APP_DIR, util.LOGS_DIR, mi.DB_PATH
        util.APP_DIR = self.tmp / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        mi.DB_PATH = util.APP_DIR / "index.db"
        from datetime import datetime
        self.now = datetime(2026, 7, 5, 12, 0, 0)

    def tearDown(self):
        util.APP_DIR, util.LOGS_DIR, mi.DB_PATH = self._app0, self._logs0, self._db0
        self._td.cleanup()

    def _meeting(self, name, *, days_ago, md=None):
        from datetime import timedelta
        folder = self.tmp / name
        folder.mkdir()
        note = folder / f"{name}.md"
        note.write_text(self._MD if md is None else md, encoding="utf-8")
        started = (self.now - timedelta(days=days_ago)).isoformat()
        return {"export_path": str(note), "folder": str(folder), "started_at": started}

    def test_arquiva_so_reunioes_antigas(self):
        recente = self._meeting("recente", days_ago=5)
        antiga = self._meeting("antiga", days_ago=90)
        n = notes.archive_old_action_items([recente, antiga], older_than_days=30,
                                           reference_date=self.now)
        self.assertEqual(n, 2)  # os 2 itens abertos da reunião antiga
        # a recente fica intacta (nada arquivado)
        self.assertEqual(notes.load_action_state(Path(recente["folder"])), {})
        # a antiga: ambos os itens agora archived
        st = notes.load_action_state(Path(antiga["folder"]))
        self.assertEqual(set(st.values()), {"archived"})
        self.assertEqual(len(st), 2)

    def test_nao_toca_itens_ja_resolvidos_ou_dispensados(self):
        antiga = self._meeting("antiga", days_ago=90)
        keys = [i["key"] for i in notes.open_action_items([antiga])]
        notes.set_action_state(Path(antiga["folder"]), keys[0], "done")
        n = notes.archive_old_action_items([antiga], older_than_days=30, reference_date=self.now)
        self.assertEqual(n, 1)  # só o item que estava open
        st = notes.load_action_state(Path(antiga["folder"]))
        self.assertEqual(st[keys[0]], "done")       # done preservado
        self.assertEqual(st[keys[1]], "archived")   # o open virou archived

    def test_reuniao_sem_data_conta_como_antiga(self):
        m = self._meeting("sem_data", days_ago=0)
        m["started_at"] = ""  # dado ausente → tratado como antigo (degradação segura)
        n = notes.archive_old_action_items([m], older_than_days=30, reference_date=self.now)
        self.assertEqual(n, 2)

    def test_older_than_zero_nao_arquiva(self):
        antiga = self._meeting("antiga", days_ago=90)
        self.assertEqual(
            notes.archive_old_action_items([antiga], older_than_days=0, reference_date=self.now), 0)
        self.assertEqual(notes.load_action_state(Path(antiga["folder"])), {})

    def test_nao_modifica_o_md(self):
        antiga = self._meeting("antiga", days_ago=90)
        before = (Path(antiga["folder"]) / "antiga.md").read_text(encoding="utf-8")
        notes.archive_old_action_items([antiga], older_than_days=30, reference_date=self.now)
        after = (Path(antiga["folder"]) / "antiga.md").read_text(encoding="utf-8")
        self.assertEqual(before, after)  # estado vive só no sidecar; .md intacto


if __name__ == "__main__":
    unittest.main(verbosity=2)
