"""Correção dos termos do vocabulário na transcrição (vocab_fix) e no resumo.

Os casos negativos vêm de uma passada em 142 mil palavras de reuniões reais
(2026-10-20): cada um foi um falso positivo de uma versão anterior das regras.
"""

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import vocab_fix  # noqa: E402

VOCAB = ("SAP ABAP BAPI BAdI CDS RAP Fiori OData ALV SE80 SE11 SE16N VA01 ME21N MIGO "
         "KNA1 SmartForms HANA user exit enhancement request transporte mandante tabela Z campo Z")
TERMOS = vocab_fix.parse_terms(VOCAB)


def fix(texto: str) -> str:
    return vocab_fix.corrigir(texto, TERMOS)[0]


class ParseTermsTests(unittest.TestCase):
    def test_espaco_separa_quando_nao_ha_virgula(self):
        self.assertIn("SE80", TERMOS)
        self.assertNotIn("Z", TERMOS)   # 1 caractere casaria qualquer letra solta

    def test_virgula_e_linha_separam_frases(self):
        self.assertEqual(vocab_fix.parse_terms("user exit, tabela Z\nSE80"), ["user exit", "tabela Z", "SE80"])

    def test_vazio_e_duplicados(self):
        self.assertEqual(vocab_fix.parse_terms("  "), [])
        self.assertEqual(vocab_fix.parse_terms("SAP sap SAP"), ["SAP"])


class CorrigeTests(unittest.TestCase):
    def test_erros_do_motor_da_apple(self):
        self.assertEqual(fix("tem algum enancement aí"), "tem algum enhancement aí")
        self.assertEqual(fix("como se fosse S 80 cê vai ver"), "como se fosse SE80 cê vai ver")
        self.assertEqual(fix("Aí smartformes de D"), "Aí SmartForms de D")

    def test_juncao_exata_e_codigos(self):
        self.assertEqual(fix("o Smart Forms, e a se 16 n"), "o SmartForms, e a SE16N")
        self.assertEqual(fix("na me 21 n e na va 01"), "na ME21N e na VA01")

    def test_sigla_ganha_a_caixa_certa(self):
        self.assertEqual(fix("o sap e o .abap, BADI e migo"), "o SAP e o .ABAP, BAdI e MIGO")

    def test_artigo_e_conjuncao_ao_redor_ficam(self):
        self.assertEqual(fix("roda a se 11 e a va 01"), "roda a SE11 e a VA01")
        self.assertEqual(fix("é o enhancement"), "é o enhancement")

    def test_falsos_positivos_reais_nao_sao_trocados(self):
        for frase in ("o dia tá bom", "tá ela aí", "o capô do carro", "ele transporta a carga",
                      "os transportes", "vai na 1 e na de 11", "a ZSM30 nova",
                      "o mandante 100 e a tabela de pedidos", "o RAP e o alvo"):
            self.assertEqual(fix(frase), frase, frase)

    def test_caixa_de_palavra_comum_e_do_falante(self):
        self.assertEqual(fix("abre a REQUEST no CAMPO"), "abre a REQUEST no CAMPO")

    def test_registra_o_que_trocou(self):
        _, feitas = vocab_fix.corrigir("S 80 e enancement", TERMOS)
        self.assertEqual([(c.original, c.termo) for c in feitas], [("S 80", "SE80"), ("enancement", "enhancement")])

    def test_sem_vocabulario_nao_mexe_em_termo_nenhum(self):
        self.assertEqual(vocab_fix.corrigir("S 80 enancement", [])[0], "S 80 enancement")


class ZDoSapTests(unittest.TestCase):
    """'Z' isolado = objeto customizado (tabela Z, programa Z, tela Z)."""

    def test_z_falado_vira_Z(self):
        self.assertEqual(fix("cria uma tabela zê e um programa z"), "cria uma tabela Z e um programa Z")
        self.assertEqual(fix("o z da empresa"), "o Z da empresa")
        self.assertEqual(fix("uma transação zé nova"), "uma transação Z nova")

    def test_ja_certo_e_nomes_ficam(self):
        for frase in ("é tudo Z mesmo", "o Zé falou", "fala com o zé", "a zmm_pedidos e a ZSM30"):
            self.assertEqual(fix(frase), frase, frase)

    def test_so_vale_para_vocabulario_de_sap(self):
        """Nem todo usuário é de SAP: para os outros, "z" solto é só uma letra."""
        self.assertEqual(vocab_fix.corrigir("a tabela zê e o z", ["Python", "React"])[0],
                         "a tabela zê e o z")
        self.assertEqual(vocab_fix.corrigir("a tabela zê", [])[0], "a tabela zê")
        self.assertEqual(vocab_fix.corrigir("a tabela zê", ["ABAP"])[0], "a tabela Z")


class PipelineTests(unittest.TestCase):
    def test_corrige_os_turnos_e_conta_no_meta(self):
        try:
            from scriba import pipeline
        except Exception:
            self.skipTest("deps de transcrição ausentes")
        from scriba.merge import Turn

        turns = [Turn(0, 1, "Participantes", "abre a S 80"), Turn(1, 2, "Eu", "e a tabela zê")]
        meta: dict = {}
        with mock.patch("builtins.print"):
            pipeline._fix_vocabulary(turns, VOCAB, meta)
        self.assertEqual([t.text for t in turns], ["abre a SE80", "e a tabela Z"])
        self.assertEqual(meta["vocab_fixes"], 2)

    def test_falha_na_correcao_nao_derruba_a_transcricao(self):
        try:
            from scriba import pipeline
        except Exception:
            self.skipTest("deps de transcrição ausentes")
        from scriba.merge import Turn

        turns = [Turn(0, 1, "Eu", "abre a S 80")]
        with mock.patch.object(vocab_fix, "corrigir", side_effect=ValueError("boom")), \
                mock.patch("builtins.print"):
            pipeline._fix_vocabulary(turns, VOCAB, {})
        self.assertEqual(turns[0].text, "abre a S 80")


class ResumoTests(unittest.TestCase):
    def setUp(self):
        from scriba import util

        self._orig = (util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH)
        d = Path(tempfile.mkdtemp(prefix="scriba_vocab_"))
        util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH = d, d / "logs", d / "config.toml"

    def tearDown(self):
        from scriba import util

        util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH = self._orig

    def test_vocabulario_vai_no_prompt_do_resumo(self):
        from scriba import config, notes

        self.assertEqual(notes._vocabulary_block(), "")   # instalação nova: vocabulário vazio
        cfg = config.load()
        config.save(dataclasses.replace(cfg, whisper=dataclasses.replace(cfg.whisper, hotwords=VOCAB)))
        bloco = notes._vocabulary_block()
        self.assertIn("SE80", bloco)
        self.assertIn("SmartForms", bloco)

    def test_correcao_ligada_por_padrao_e_desligavel(self):
        from scriba import config

        cfg = config.load()
        self.assertTrue(cfg.whisper.vocab_fix)
        config.save(dataclasses.replace(cfg, whisper=dataclasses.replace(cfg.whisper, vocab_fix=False)))
        self.assertFalse(config.load().whisper.vocab_fix)


if __name__ == "__main__":
    unittest.main()
