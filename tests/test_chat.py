"""Testes do chat (#22): payload SÓ-RESUMO (a transcrição nunca é despejada) + self-ask
RAG (planner/parse/answer via chat_rag, com ai.complete falso). Não abre janela."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import chat_rag  # noqa: E402
from scriba.chat_ui import ChatWindow, _MAX_TURNS, _ROLE_SYS, _WARN_AFTER_TURNS  # noqa: E402


class SummaryPayloadTests(unittest.TestCase):
    """_summary_payload é o caminho busca-desligada: resumo + histórico, NUNCA transcrição."""

    def _cw(self, summary="RESUMO", history=None):
        cw = ChatWindow.__new__(ChatWindow)  # sem __init__ (não abre Toplevel)
        cw._summary = summary
        cw._history = history or []
        return cw

    def test_inclui_resumo(self):
        p = self._cw(summary="RESUMO AQUI")._summary_payload("q?")
        self.assertIn("RESUMO AQUI", p)

    def test_teto_de_seguranca_do_historico(self):
        hist = [(f"q{i}", f"a{i}") for i in range(60)]   # acima do teto (_MAX_TURNS=50)
        p = self._cw(history=hist)._summary_payload("nova")
        self.assertIn("q59", p)                          # recentes presentes
        self.assertIn(f"q{60 - _MAX_TURNS}", p)          # q10 = mais antigo dentro do teto
        self.assertNotIn("q0", p)                         # além do teto saem
        self.assertNotIn(f"q{60 - _MAX_TURNS - 1}", p)   # q9 fora

    def test_termina_na_pergunta(self):
        p = self._cw()._summary_payload("o que decidiu?")
        self.assertTrue(p.rstrip().endswith("Pergunta: o que decidiu?\nResposta:"))


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


class _FakeConv:
    """Text widget fake p/ testar _clear_context (limpa o widget) sem abrir janela."""
    def configure(self, **kw):
        pass

    def delete(self, start, end):
        pass


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


class WarnAndClearTests(unittest.TestCase):
    """Aviso de custo ao chegar em _WARN_AFTER_TURNS + comando /clear. Stubs de _append/_set_busy
    (não abre janela): validamos só a lógica de estado e as mensagens emitidas."""

    def _cw(self):
        cw = ChatWindow.__new__(ChatWindow)
        cw._history = []
        cw._warned = False
        cw._thinking = False   # _answered chama _stop_thinking; sem thinking ativo, é no-op
        cw._think_after = None
        cw._appended = []
        cw._append = lambda text, role: cw._appended.append((text, role))
        cw._append_md = lambda md: cw._appended.append((md, "assist"))  # resposta renderizada
        cw._set_busy = lambda b: None
        cw.conv = _FakeConv()   # _clear_context limpa o widget
        return cw

    def _warn_lines(self, cw):
        return [text for text, _role in cw._appended if "/clear" in text and "limpo" not in text]

    def _clear_lines(self, cw):
        return [text for text, _role in cw._appended if "limpo" in text]

    def test_avisa_ao_chegar_no_limite(self):
        cw = self._cw()
        for i in range(_WARN_AFTER_TURNS):
            cw._answered(f"q{i}", f"a{i}")
        self.assertEqual(len(self._warn_lines(cw)), 1)   # avisou 1x exatamente ao cruzar o limite
        self.assertTrue(cw._warned)

    def test_nao_avisa_antes_do_limite(self):
        cw = self._cw()
        for i in range(_WARN_AFTER_TURNS - 1):
            cw._answered(f"q{i}", f"a{i}")
        self.assertEqual(self._warn_lines(cw), [])
        self.assertFalse(cw._warned)

    def test_aviso_nao_repete(self):
        cw = self._cw()
        for i in range(_WARN_AFTER_TURNS + 4):
            cw._answered(f"q{i}", f"a{i}")
        self.assertEqual(len(self._warn_lines(cw)), 1)   # uma vez só, mesmo continuando

    def test_resposta_vazia_nao_conta_nem_avisa(self):
        cw = self._cw()
        for _ in range(_WARN_AFTER_TURNS + 2):
            cw._answered("q", None)                       # falha da IA: não entra no histórico
        self.assertEqual(cw._history, [])
        self.assertFalse(cw._warned)

    def test_clear_zera_historico_e_avisa(self):
        cw = self._cw()
        for i in range(_WARN_AFTER_TURNS):
            cw._answered(f"q{i}", f"a{i}")
        cw._clear_context()
        self.assertEqual(cw._history, [])
        self.assertFalse(cw._warned)
        self.assertEqual(len(self._clear_lines(cw)), 1)
        self.assertTrue(any(role == _ROLE_SYS and "limpo" in text for text, role in cw._appended))

    def test_aviso_rearmado_apos_clear(self):
        cw = self._cw()
        for i in range(_WARN_AFTER_TURNS):
            cw._answered(f"q{i}", f"a{i}")
        cw._clear_context()
        for i in range(_WARN_AFTER_TURNS):
            cw._answered(f"r{i}", f"b{i}")
        self.assertEqual(len(self._warn_lines(cw)), 2)   # avisou de novo no 2º ciclo


if __name__ == "__main__":
    unittest.main(verbosity=2)
