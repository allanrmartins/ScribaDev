"""Gravação de teste (bandeja > "Gravar teste"): processa inteira, mas não entra no
trabalho do usuário (horas, pendências, busca, vocabulário) e some em 24 h."""

from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from scriba import meetings_index, notes, prints, retention, timesheet_suggest

_TS = types.SimpleNamespace(min_meeting_minutes=0, round_minutes=15)


def _meta(**extra) -> dict:
    return {"status": "done", "started_at": "2026-11-10T09:00:00",
            "ended_at": "2026-11-10T09:30:00", "duration_seconds": 1800.0,
            "title": "Call", **extra}


class ForaDoTrabalhoTests(unittest.TestCase):
    def test_nao_vira_apontamento_de_horas(self):
        self.assertIsNotNone(timesheet_suggest.suggestion_from_meta(_meta(), _TS))
        self.assertIsNone(timesheet_suggest.suggestion_from_meta(_meta(test=True), _TS))

    def test_fica_fora_do_indice_de_busca(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_teste_"))
        self.addCleanup(shutil.rmtree, d, True)
        (d / "meta.json").write_text(json.dumps(_meta(test=True)), encoding="utf-8")
        self.assertIsNone(meetings_index._extract(d))
        (d / "meta.json").write_text(json.dumps(_meta()), encoding="utf-8")
        self.assertIsNotNone(meetings_index._extract(d))

    def test_nao_ensina_vocabulario(self):
        with mock.patch("scriba.vocab_learn.record") as rec:
            notes._learn_terms(Path("x"), _meta(test=True), ["ZFI_BOLETO"])
        rec.assert_not_called()

    def test_nota_de_teste_reconhecida_pelo_frontmatter(self):
        self.assertTrue(notes.is_test_note("---\ntitulo: TESTE - X\norigem: scriba\nteste: sim\n---\n"))
        self.assertFalse(notes.is_test_note("---\ntitulo: X\norigem: scriba\n---\n"))
        self.assertFalse(notes.is_test_note("# teste: sim\n"))  # só vale no frontmatter


class NotaDeTesteTests(unittest.TestCase):
    """build_notes com a IA simulada: título com a etiqueta e `teste: sim`."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="scriba_teste_"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.rec = self.d / "rec" / "09-00"
        self.rec.mkdir(parents=True)
        (self.rec / "transcript.json").write_text(json.dumps(
            [{"start": 1.0, "end": 2.0, "speaker": "Eu", "text": "testando"}]), encoding="utf-8")

    def _build(self, meta: dict) -> str:
        (self.rec / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        export = self.d / "notas"
        cfg = types.SimpleNamespace(output=types.SimpleNamespace(resolved_export_dir=lambda: export))
        with mock.patch("scriba.notes.load", return_value=cfg), \
                mock.patch("scriba.notes.generate_summary", return_value=("## Objetivo\nX.\n", "Titulo", "")), \
                mock.patch("scriba.notes.load_context_note", return_value=""), \
                mock.patch("scriba.meetings_index.index_meeting"):
            return notes.build_notes(self.rec).read_text(encoding="utf-8")

    def test_etiqueta_no_titulo_e_no_frontmatter(self):
        md = self._build({**_meta(test=True), "status": "transcribed"})
        self.assertIn("titulo: TESTE - Titulo\n", md)
        self.assertIn("# TESTE - Titulo\n", md)
        self.assertTrue(notes.is_test_note(md))
        meta = json.loads((self.rec / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["title"], meta["test"]), ("TESTE - Titulo", True))

    def test_reprocessar_nao_repete_a_etiqueta(self):
        self._build({**_meta(test=True), "status": "transcribed"})
        meta = json.loads((self.rec / "meta.json").read_text(encoding="utf-8"))
        meta["status"] = "transcribed"
        export = self.d / "notas"
        cfg = types.SimpleNamespace(output=types.SimpleNamespace(resolved_export_dir=lambda: export))
        (self.rec / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        with mock.patch("scriba.notes.load", return_value=cfg), \
                mock.patch("scriba.notes.generate_summary", return_value=("## Objetivo\nX.\n", None, None)), \
                mock.patch("scriba.notes.load_context_note", return_value=""), \
                mock.patch("scriba.meetings_index.index_meeting"):
            md = notes.build_notes(self.rec).read_text(encoding="utf-8")
        self.assertIn("titulo: TESTE - Titulo\n", md)

    def test_reuniao_normal_sem_etiqueta(self):
        md = self._build({**_meta(), "status": "transcribed"})
        self.assertIn("titulo: Titulo\n", md)
        self.assertFalse(notes.is_test_note(md))


class LimpezaDeTestesTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="scriba_teste_"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.recs = self.d / "gravacoes"
        self.notas = self.d / "notas"
        self.notas.mkdir()
        self.cfg = types.SimpleNamespace(
            output=types.SimpleNamespace(resolved_recordings_dir=lambda: self.recs))
        self.now = datetime(2026, 11, 11, 12, 0)

    def _rec(self, hhmm: str, ended: datetime, **extra) -> tuple[Path, Path]:
        folder = self.recs / "2026" / "11" / "10" / hhmm
        (folder / "prints").mkdir(parents=True)
        (folder / "prints" / "print-1.png").write_bytes(b"png")
        note = self.notas / f"2026-11-10_{hhmm}_reuniao.md"
        note.write_text("nota", encoding="utf-8")
        note.with_suffix(".md.bak").write_text("velha", encoding="utf-8")
        pdir = self.notas / prints.export_dir_name(note)
        pdir.mkdir()
        (pdir / "print-1.png").write_bytes(b"png")
        meta = {"status": "done", "started_at": (ended - timedelta(minutes=5)).isoformat(),
                "ended_at": ended.isoformat(), "export_path": str(note), **extra}
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return folder, note

    def test_teste_com_mais_de_24h_some_inteiro(self):
        velho, nota = self._rec("08-00", self.now - timedelta(hours=25), test=True)
        with mock.patch("scriba.meetings_index.remove_meeting") as rm:
            out = retention.purge_test_meetings(self.cfg, now=self.now)
        self.assertEqual(out, [velho])
        rm.assert_called_once_with(velho)
        self.assertFalse(nota.exists())
        self.assertFalse(nota.with_suffix(".md.bak").exists())
        self.assertFalse((self.notas / prints.export_dir_name(nota)).exists())
        self.assertEqual(list(self.recs.iterdir()), [])  # pastas de data vazias saem junto

    def test_preserva_teste_recente_em_andamento_e_reuniao_normal(self):
        recente, n1 = self._rec("09-00", self.now - timedelta(hours=2), test=True)
        andando, n2 = self._rec("09-10", self.now - timedelta(hours=30), test=True)
        meta = json.loads((andando / "meta.json").read_text(encoding="utf-8"))
        meta["status"] = "summarizing"
        (andando / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        normal, n3 = self._rec("09-20", self.now - timedelta(days=10))
        with mock.patch("scriba.meetings_index.remove_meeting"):
            self.assertEqual(retention.purge_test_meetings(self.cfg, now=self.now), [])
        for folder, nota in ((recente, n1), (andando, n2), (normal, n3)):
            self.assertTrue((folder / "meta.json").exists())
            self.assertTrue(nota.exists())

    def test_teste_que_falhou_tambem_some(self):
        falhou, _ = self._rec("10-00", self.now - timedelta(hours=26), test=True, status="failed")
        with mock.patch("scriba.meetings_index.remove_meeting"):
            self.assertEqual(retention.purge_test_meetings(self.cfg, now=self.now), [falhou])


if __name__ == "__main__":
    unittest.main(verbosity=2)
