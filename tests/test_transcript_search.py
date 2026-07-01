"""Testes do FTS :memory: intra-transcrição (self-ask RAG do chat). Sem IA, sem janela."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.transcript_search import TranscriptSearcher, chunk_transcript  # noqa: E402


def _turns(*items):
    return "\n\n".join(f"**[{ts}] {sp}:** {tx}" for ts, sp, tx in items)


TR = _turns(
    ("00:00:01", "Ana", "Bom dia, vamos falar do prazo de entrega do módulo fiscal."),
    ("00:00:12", "Bruno", "Acho que conseguimos fechar até sexta-feira sem problema."),
    ("00:00:25", "Ana", "E o orçamento aprovado, ficou em quanto no total?"),
    ("00:00:33", "Bruno", "Cinquenta mil reais, o cliente aprovou ontem à tarde."),
    ("00:01:05", "Ana", "Fechado. A Carla cuida da homologação em produção."),
)


class ChunkTests(unittest.TestCase):
    def test_preserva_carimbos_e_cobre_tudo(self):
        joined = "\n".join(chunk_transcript(TR, max_chars=200))
        self.assertIn("[00:00:01]", joined)   # começo
        self.assertIn("[00:01:05]", joined)   # fim
        self.assertIn("Cinquenta mil", joined)

    def test_agrupa_ate_o_teto(self):
        poucos = chunk_transcript(TR, max_chars=1000)   # cabe tudo em 1-2 blocos
        muitos = chunk_transcript(TR, max_chars=90)      # ~1 turno por bloco
        self.assertLess(len(poucos), len(muitos))

    def test_vazio(self):
        self.assertEqual(chunk_transcript(""), [])
        self.assertEqual(chunk_transcript("   \n\n  "), [])


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.s = TranscriptSearcher(TR, max_chars=200)

    def tearDown(self):
        self.s.close()

    def test_acha_por_palavra_chave(self):
        self.assertTrue(any("orçamento" in h for h in self.s.search(["orçamento"])))

    def test_multiplas_queries_unem_resultados(self):
        blob = "\n".join(self.s.search(["prazo", "homologação"]))
        self.assertIn("prazo", blob)
        self.assertIn("homologação", blob)

    def test_ordem_cronologica(self):
        # 'prazo' casa o início, 'homologação' o fim; o retorno vem em ordem da fala
        blob = "\n".join(self.s.search(["prazo", "homologação"]))
        self.assertLess(blob.index("prazo"), blob.index("homologação"))

    def test_sem_match_retorna_vazio(self):
        self.assertEqual(self.s.search(["xyzzynaoexiste"]), [])

    def test_query_vazia_ou_degenerada_nao_quebra(self):
        self.assertEqual(self.s.search([""]), [])
        self.assertEqual(self.s.search(["   "]), [])
        self.assertIsInstance(self.s.search(["- , !"]), list)

    def test_aceita_string_unica(self):
        self.assertTrue(any("orçamento" in h for h in self.s.search("orçamento")))

    def test_transcricao_vazia(self):
        s = TranscriptSearcher("")
        self.assertEqual(s.search(["qualquer"]), [])
        s.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
