"""Resumo map-reduce (#22): garante que NENHUMA parte da transcrição deixa de ser lida ao
gerar o resumo. Núcleo puro (_map_reduce_notes) + fiação (generate_summary com IA falsa)."""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import notes  # noqa: E402
from scriba.transcript_search import chunk_transcript  # noqa: E402


def _tr(n, marker="MARK"):
    return "\n\n".join(f"**[00:00:{i:02d}] F:** {marker}{i} conteudo do turno" for i in range(n))


class MapReduceCoreTests(unittest.TestCase):
    """_map_reduce_notes é puro (extract injetado): sem IA, sem IO."""

    def test_cobre_toda_a_transcricao(self):
        tr = _tr(30)
        seen = []

        def extract(body, i, total):
            seen.append(body)
            return f"notas {i}"

        out = notes._map_reduce_notes(tr, extract, map_chunk_chars=150, reduce_input_chars=100_000)
        blob = "\n".join(seen)
        for i in range(30):
            self.assertIn(f"MARK{i}", blob)   # NENHUM turno ficou de fora do map
        self.assertIsNotNone(out)

    def test_aborta_se_uma_parte_falha(self):
        tr = _tr(20)

        def extract(body, i, total):
            return None if i == 2 else "ok"   # a 2ª parte falha

        self.assertIsNone(notes._map_reduce_notes(tr, extract, map_chunk_chars=100))

    def test_condensa_quando_notas_nao_cabem_no_reduce(self):
        tr = _tr(60)
        calls = {"n": 0}

        def extract(body, i, total):
            calls["n"] += 1
            return "n" * 120

        out = notes._map_reduce_notes(tr, extract, map_chunk_chars=400, reduce_input_chars=300)
        self.assertIsNotNone(out)
        self.assertLessEqual(len(out), 300)                     # convergiu ao teto do reduce
        parts0 = len(chunk_transcript(tr, max_chars=400))
        self.assertGreater(calls["n"], parts0)                  # houve nível extra de condensação


class GenerateSummaryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)   # sem meta.json → meta={}, duração 0
        self._cfg = types.SimpleNamespace(
            summary=types.SimpleNamespace(enabled=True, timeout_seconds=60)
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, transcript, fake):
        with mock.patch("scriba.ai.complete", side_effect=fake), \
             mock.patch.object(notes, "load", return_value=self._cfg), \
             mock.patch.object(notes, "load_summary_prompt", return_value="PROMPT"):
            return notes.generate_summary(transcript, self.folder)

    def test_curto_uma_chamada_transcricao_crua(self):
        calls = []

        def fake(system, payload, **kw):
            calls.append((system, payload))
            return "TITULO: Assunto X\nCLIENTE: ACME\n\nCorpo do resumo."

        resumo, titulo, cliente = self._run("**[00:00:01] A:** Oi, tudo certo?", fake)
        self.assertEqual(len(calls), 1)                          # 1 chamada só
        self.assertIn("=== TRANSCRIÇÃO ===", calls[0][1])
        self.assertNotIn(notes._MAP_SYSTEM, [c[0] for c in calls])  # não usou o map
        self.assertEqual(titulo, "Assunto X")
        self.assertEqual(cliente, "ACME")
        self.assertIn("Corpo do resumo", resumo)

    def test_longo_dispara_map_reduce(self):
        big = "\n\n".join(f"**[00:{i // 60:02d}:{i % 60:02d}] A:** " + "palavra " * 40
                          for i in range(600))       # ~200k chars > _SINGLE_SHOT_CHARS
        self.assertGreater(len(big), notes._SINGLE_SHOT_CHARS)
        calls = []

        def fake(system, payload, **kw):
            calls.append((system, payload))
            if system == notes._MAP_SYSTEM:
                return f"NOTAS: {payload[:20]}"
            return "TITULO: Reuniao Longa\nCLIENTE: ACME\n\nResumo consolidado final."

        resumo, titulo, cliente = self._run(big, fake)
        maps = [c for c in calls if c[0] == notes._MAP_SYSTEM]
        self.assertGreaterEqual(len(maps), 2)                    # fatiou em várias partes
        self.assertEqual(calls[-1][0], notes.SYSTEM_PROMPT)      # última = reduce estruturado
        self.assertIn("=== NOTAS DAS PARTES", calls[-1][1])      # reduce recebeu as notas
        self.assertEqual(titulo, "Reuniao Longa")
        self.assertEqual(cliente, "ACME")
        self.assertIn("Resumo consolidado", resumo)

    def test_longo_aborta_se_parte_falha(self):
        big = "\n\n".join(f"**[00:{i // 60:02d}:{i % 60:02d}] A:** " + "palavra " * 40
                          for i in range(600))

        def fake(system, payload, **kw):
            if system == notes._MAP_SYSTEM:
                return None                                       # toda parte falha
            return "TITULO: X\nCLIENTE: Y\n\nnao deveria chegar aqui"

        self.assertEqual(self._run(big, fake), (None, None, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
