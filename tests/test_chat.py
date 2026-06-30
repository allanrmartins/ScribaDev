"""Teste de ChatWindow._build_payload (#22): contexto + histórico multi-turno.

Não cria janela (usa __new__); só exercita a montagem do payload, que é o que
mantém o multi-turno sem precisar de sessão no provider de IA.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.chat_ui import ChatWindow  # noqa: E402


class BuildPayloadTests(unittest.TestCase):
    def _cw(self, context, history):
        cw = ChatWindow.__new__(ChatWindow)  # sem __init__ (não abre Toplevel)
        cw._context = context
        cw._history = history
        return cw

    def test_inclui_contexto_e_termina_na_pergunta(self):
        p = self._cw("RESUMO E TRANSCRICAO", [])._build_payload("o que decidiu?")
        self.assertIn("RESUMO E TRANSCRICAO", p)
        self.assertTrue(p.rstrip().endswith("Pergunta: o que decidiu?\nResposta:"))

    def test_historico_multi_turno_entra_no_payload(self):
        p = self._cw("CTX", [("q1", "a1"), ("q2", "a2")])._build_payload("q3")
        self.assertIn("Pergunta: q1\nResposta: a1", p)
        self.assertIn("Pergunta: q2\nResposta: a2", p)
        self.assertIn("Pergunta: q3\nResposta:", p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
