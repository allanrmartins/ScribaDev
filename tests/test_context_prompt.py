"""Testes de scriba.context_prompt (#3): prompt de contexto puro, sem UI.

Roda sem dependências externas (módulo só usa stdlib) — o mesmo código que o
Scriba nuvem vai reusar.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import context_prompt as cp  # noqa: E402

NOTE = """---
titulo: Ajuste na VA01
cliente: Acme
data: 2026-06-10T19:45:00
duracao_minutos: 12
origem: scriba
---

# Ajuste na VA01

*2026-06-10 19:45 · 12 min · Cliente: Acme*

> **Contexto para IA:** registro técnico de uma reunião...

## Objetivo
**Tipo:** mudança em existente
Ajustar a VA01 para validar X.

## Regras de negócio
RN-01: algo importante.

## Transcrição completa

**[00:00:01] Eu:** bla bla
**[00:00:05] Participante 1:** resposta
"""


class BuildContextPromptTests(unittest.TestCase):
    def setUp(self):
        self.out = cp.build_context_prompt(NOTE)

    def test_tem_moldura_instrutiva(self):
        self.assertIn("CONTEXTO de apoio", self.out)
        self.assertIn("NÃO é um documento canônico", self.out)
        self.assertIn("ABSORVA", self.out)

    def test_cabecalho_com_titulo_cliente_data(self):
        self.assertIn('Contexto de reunião — "Ajuste na VA01"', self.out)
        self.assertIn("Cliente: Acme", self.out)
        self.assertIn("Data: 2026-06-10 19:45", self.out)  # T trocado por espaço, sem segundos

    def test_inclui_secoes_do_resumo(self):
        self.assertIn("## Objetivo", self.out)
        self.assertIn("## Regras de negócio", self.out)
        self.assertIn("mudança em existente", self.out)

    def test_exclui_transcricao_e_frontmatter(self):
        self.assertNotIn("Transcrição completa", self.out)
        self.assertNotIn("[00:00:01]", self.out)
        self.assertNotIn("origem: scriba", self.out)
        self.assertNotIn("duracao_minutos", self.out)

    def test_exclui_callout_antigo_h1_e_metaline(self):
        self.assertNotIn("Contexto para IA", self.out)   # callout antigo não entra
        self.assertNotIn("· 12 min ·", self.out)          # linha de metadados não entra

    def test_meta_sobrepoe_frontmatter(self):
        out = cp.build_context_prompt(NOTE, {"title": "Override", "client": "OutroCliente"})
        self.assertIn('"Override"', out)
        self.assertIn("Cliente: OutroCliente", out)

    def test_nota_sem_resumo_nao_quebra(self):
        out = cp.build_context_prompt("---\ntitulo: X\n---\n\n# X\n\n> Resumo indisponível\n")
        self.assertIn('"X"', out)
        self.assertIn("ainda não tem resumo", out)

    def test_sem_frontmatter_usa_titulo_padrao(self):
        out = cp.build_context_prompt("## Objetivo\nfazer algo")
        self.assertIn("(sem título)", out)
        self.assertIn("## Objetivo", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
