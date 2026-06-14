"""Testes de scriba.notes.split_header — tolerância a preâmbulo do modelo (issue #18).

Roda sem dependências externas:  python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.notes import split_header  # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
