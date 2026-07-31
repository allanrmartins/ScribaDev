"""Testes da interface de agente da CLI (skill scriba-reunioes):

`search --json` (registros completos + snippet FTS) e `show` (resumo/transcrição/
--json com participantes e pendências, por pasta OU por id do índice). Tudo isolado
em tempdir (APP_DIR/DB_PATH redirecionados) — nunca toca o index.db real.

Roda sem dependências externas:  python -m unittest discover -s tests
"""

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import cli, speakers, util  # noqa: E402
from scriba import meetings_index as mi  # noqa: E402


class CliAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_cliag_"))
        self._app0, self._logs0, self._store0, self._db0 = (
            util.APP_DIR, util.LOGS_DIR, speakers.STORE_PATH, mi.DB_PATH)
        util.APP_DIR = self.tmp / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        speakers.STORE_PATH = util.APP_DIR / "speakers.json"
        mi.DB_PATH = self.tmp / "index.db"
        self.rec = self.tmp / "rec"
        self.rec.mkdir(parents=True)

    def tearDown(self):
        util.APP_DIR, util.LOGS_DIR, speakers.STORE_PATH, mi.DB_PATH = (
            self._app0, self._logs0, self._store0, self._db0)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_meeting(self, name, *, started_at, title="Reunião X", client="",
                      body="assunto generico", pendencias=None,
                      transcript="**[00:03:10] Eu:** detalhe TRANSCRITOSO"):
        folder = self.rec / name
        folder.mkdir(parents=True, exist_ok=True)
        meta = {
            "status": "done", "started_at": started_at, "ended_at": started_at,
            "duration_seconds": 600, "title": title, "client": client,
            "export_path": str(folder / "nota.md"), "meeting_title": "",
        }
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        lines = [f"# {title}", "", "## Resumo", body, "", "## Participantes", "",
                 "**Presentes:**", "- **Alice** — dev"]
        if pendencias:
            lines += ["", "## Pendências e Ações"] + [f"- {p}" for p in pendencias]
        lines += ["", mi._TRANSCRIPT_MARKER, "", transcript, ""]
        (folder / "notas.md").write_text("\n".join(lines), encoding="utf-8")
        return folder

    def _run(self, argv) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(argv)
        return rc, buf.getvalue()

    # -- search --json --------------------------------------------------------
    def test_search_json_devolve_registros_com_id_e_snippet(self):
        f = self._make_meeting("a", started_at="2026-07-01T09:00:00",
                               title="Kickoff", client="ACME",
                               body="fechamos o ASSUNTOUNICOXYZ do projeto")
        self.assertTrue(mi.index_meeting(f))
        rc, out = self._run(["search", "ASSUNTOUNICOXYZ", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        r = data[0]
        self.assertEqual(r["client"], "ACME")
        self.assertIsInstance(r["id"], int)
        self.assertIn("«ASSUNTOUNICOXYZ»", r["snippet"])
        self.assertEqual(r["participants"][0]["name"], "Alice")

    def test_search_json_sem_query_nao_tem_snippet_e_vazio_e_lista_vazia(self):
        f = self._make_meeting("a", started_at="2026-07-01T09:00:00", client="ACME")
        self.assertTrue(mi.index_meeting(f))
        rc, out = self._run(["search", "--client", "ACME", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertNotIn("snippet", data[0])
        # sem resultados: JSON vazio (não a mensagem humana "nenhuma reunião")
        rc, out = self._run(["search", "--client", "NAOEXISTE", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), [])

    # -- show -----------------------------------------------------------------
    def test_show_resumo_por_pasta_e_transcript(self):
        f = self._make_meeting("a", started_at="2026-07-01T09:00:00",
                               body="corpo do RESUMAO")
        rc, out = self._run(["show", str(f)])
        self.assertEqual(rc, 0)
        self.assertIn("RESUMAO", out)
        self.assertNotIn("TRANSCRITOSO", out)  # resumo não vaza transcrição
        rc, out = self._run(["show", str(f), "--transcript"])
        self.assertEqual(rc, 0)
        self.assertIn("TRANSCRITOSO", out)
        self.assertNotIn("RESUMAO", out)

    def test_show_json_por_id_com_participantes_e_pendencias(self):
        f = self._make_meeting("a", started_at="2026-07-01T09:00:00",
                               title="Kickoff", client="ACME",
                               pendencias=["**Alice:** enviar proposta"])
        self.assertTrue(mi.index_meeting(f))
        mid = mi.search(client="ACME")[0]["id"]
        rc, out = self._run(["show", str(mid), "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["folder"], str(f))
        self.assertEqual(data["client"], "ACME")
        self.assertEqual(data["participants"]["present"][0]["name"], "Alice")
        self.assertEqual(len(data["action_items"]), 1)
        self.assertEqual(data["action_items"][0]["state"], "open")
        self.assertIn("Resumo", data["summary"])
        self.assertNotIn("transcript", data)  # só com --transcript
        rc, out = self._run(["show", str(mid), "--json", "--transcript"])
        self.assertEqual(rc, 0)
        self.assertIn("TRANSCRITOSO", json.loads(out)["transcript"])

    def test_show_alvo_inexistente_e_sem_nota(self):
        rc, out = self._run(["show", "99887"])
        self.assertEqual(rc, 2)
        self.assertIn("não encontrado", out)
        vazia = self.rec / "semnota"
        vazia.mkdir()
        rc, out = self._run(["show", str(vazia)])
        self.assertEqual(rc, 1)
        self.assertIn("notas.md", out)

    # -- meetings_index: novas APIs ------------------------------------------
    def test_get_folder_e_split_note(self):
        f = self._make_meeting("a", started_at="2026-07-01T09:00:00", client="ACME")
        self.assertTrue(mi.index_meeting(f))
        mid = mi.search(client="ACME")[0]["id"]
        self.assertEqual(mi.get_folder(mid), str(f))
        self.assertIsNone(mi.get_folder(123456))
        summary, transcript = mi.split_note((f / "notas.md").read_text(encoding="utf-8"))
        self.assertIn("Resumo", summary)
        self.assertIn("TRANSCRITOSO", transcript)
        self.assertNotIn("TRANSCRITOSO", summary)

    def test_search_snippets_opcional(self):
        f = self._make_meeting("a", started_at="2026-07-01T09:00:00",
                               body="tema BUSCAVELZZ aqui")
        self.assertTrue(mi.index_meeting(f))
        com = mi.search(query="BUSCAVELZZ", snippets=True)
        self.assertIn("«BUSCAVELZZ»", com[0]["snippet"])
        sem = mi.search(query="BUSCAVELZZ")
        self.assertNotIn("snippet", sem[0])


if __name__ == "__main__":
    unittest.main()
