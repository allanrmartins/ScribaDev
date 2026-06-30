"""Teste de ChatWindow._build_payload (#22): só-resumo por padrão, toggle de
transcrição e cap de histórico. Não cria janela (usa __new__)."""

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.chat_ui import ChatWindow, _MAX_TURNS  # noqa: E402


class BuildPayloadTests(unittest.TestCase):
    def _cw(self, summary="RESUMO", transcript=None, history=None, include=False):
        cw = ChatWindow.__new__(ChatWindow)  # sem __init__ (não abre Toplevel)
        cw._summary = summary
        cw._transcript = transcript
        cw._history = history or []
        cw._include_transcript = types.SimpleNamespace(get=lambda: include)  # finge a BooleanVar
        return cw

    def test_so_resumo_por_padrao(self):
        p = self._cw(summary="RESUMO AQUI", transcript="TRANSCRICAO LONGA", include=False)._build_payload("q?")
        self.assertIn("RESUMO AQUI", p)
        self.assertNotIn("TRANSCRICAO LONGA", p)  # transcrição NÃO entra por padrão

    def test_inclui_transcricao_quando_ligado(self):
        p = self._cw(summary="RESUMO", transcript="TRANSCRICAO X", include=True)._build_payload("q?")
        self.assertIn("RESUMO", p)
        self.assertIn("TRANSCRICAO X", p)

    def test_cap_de_historico(self):
        hist = [(f"q{i}", f"a{i}") for i in range(10)]
        p = self._cw(history=hist)._build_payload("nova")
        self.assertIn("q9", p)            # últimas trocas presentes
        self.assertNotIn("q0", p)         # as antigas (além do cap) saem
        self.assertNotIn(f"q{10 - _MAX_TURNS - 1}", p)  # q3 fora quando _MAX_TURNS=6

    def test_termina_na_pergunta(self):
        p = self._cw()._build_payload("o que decidiu?")
        self.assertTrue(p.rstrip().endswith("Pergunta: o que decidiu?\nResposta:"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
