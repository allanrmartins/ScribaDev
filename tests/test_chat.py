"""Testes do self-ask RAG do chat (#22): planner/parse/answer via chat_rag (puro), com
ai.complete falso. A lógica da janela (payload, aviso de custo, /clear) é testada na
versão Qt em test_qt_chat (a ChatWindow tkinter saiu no corte #53)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import chat_rag  # noqa: E402


class ParseQueriesTests(unittest.TestCase):
    def test_sem_busca(self):
        self.assertEqual(chat_rag.parse_queries("SEM_BUSCA"), [])
        self.assertEqual(chat_rag.parse_queries("O resumo basta.\nSEM_BUSCA"), [])

    def test_limpa_marcadores_numeracao_rotulo(self):
        out = chat_rag.parse_queries("- prazo entrega\n2) orçamento valor\nTermos: deadline")
        self.assertEqual(out, ["prazo entrega", "orçamento valor", "deadline"])

    def test_cap_quatro(self):
        out = chat_rag.parse_queries("\n".join(f"t{i}" for i in range(8)))
        self.assertEqual(len(out), 4)


class _FakeSearcher:
    def __init__(self, snippets):
        self._snips = snippets
        self.q = None

    def search(self, queries, **kw):
        self.q = queries
        return self._snips


class RespondTests(unittest.TestCase):
    def test_sem_busca_responde_do_resumo(self):
        outs = ["SEM_BUSCA", "resposta do resumo"]
        seen = []

        def fake(system, payload, **kw):
            seen.append(payload)
            return outs[len(seen) - 1]

        fs = _FakeSearcher(["NAO DEVIA APARECER"])
        with mock.patch.object(chat_rag.ai, "complete", side_effect=fake):
            out = chat_rag.respond("RESUMO", [], "oi?", searcher=fs, timeout=10, model=None)
        self.assertEqual(out, "resposta do resumo")
        self.assertEqual(len(seen), 2)                 # planner + answer
        self.assertIsNone(fs.q)                        # SEM_BUSCA => não buscou
        self.assertNotIn("NAO DEVIA APARECER", seen[1])

    def test_busca_injeta_trechos_na_resposta(self):
        outs = ["prazo\nentrega", "RESPOSTA FINAL"]
        seen = []

        def fake(system, payload, **kw):
            seen.append(payload)
            return outs[len(seen) - 1]

        fs = _FakeSearcher(["**[00:01:00] X:** trecho relevante"])
        with mock.patch.object(chat_rag.ai, "complete", side_effect=fake):
            out = chat_rag.respond("RESUMO", [], "quando entrega?", searcher=fs, timeout=10, model=None)
        self.assertEqual(out, "RESPOSTA FINAL")
        self.assertEqual(fs.q, ["prazo", "entrega"])   # queries do planner chegaram à busca
        self.assertIn("trecho relevante", seen[1])      # trecho entrou no prompt de resposta

    def test_planner_falho_cai_na_pergunta(self):
        outs = [None, "ANSWER"]
        seen = []

        def fake(system, payload, **kw):
            seen.append(payload)
            return outs[len(seen) - 1]

        fs = _FakeSearcher(["trecho"])
        with mock.patch.object(chat_rag.ai, "complete", side_effect=fake):
            out = chat_rag.respond("RESUMO", [], "quando entrega?", searcher=fs, timeout=10, model=None)
        self.assertEqual(out, "ANSWER")
        self.assertEqual(fs.q, ["quando entrega?"])    # fallback: busca direto pela pergunta


if __name__ == "__main__":
    unittest.main(verbosity=2)
