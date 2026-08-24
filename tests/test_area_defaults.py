"""O app deixa de nascer SAP/ABAP, sem mudar a ata de quem já usava (#181).

Até a 1.4.10 o padrão embutido do prompt do resumo e do cabeçalho "Contexto para
IA" era o do perfil SAP/ABAP, e não existia arquivo em disco: quem instalava e não
abria o assistente de perfil recebia "reunião SAP/ABAP" em toda ata, mesmo sendo de
outra área, sem lugar na interface para trocar o cabeçalho.

O padrão virou o template genérico. Como isso mudaria a ata de quem já usa o app,
a migração congela os textos antigos em arquivo na primeira subida. As duas metades
são testadas aqui: instalação nova nasce genérica, instalação existente continua
exatamente como estava.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import notes, promptgen, util  # noqa: E402


class _AppDirIsolado(unittest.TestCase):
    """APP_DIR de mentira: os caminhos são calculados no import do util."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        raiz = Path(self._tmp.name)
        self._orig = (util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH,
                      util.STATE_PATH, util.PROMPT_PATH, util.CONTEXT_PATH)
        util.APP_DIR = raiz
        util.LOGS_DIR = raiz / "logs"
        util.CONFIG_PATH = raiz / "config.toml"
        util.STATE_PATH = raiz / "state.json"
        util.PROMPT_PATH = raiz / "prompt.md"
        util.CONTEXT_PATH = raiz / "context.md"
        self.raiz = raiz

    def tearDown(self):
        (util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH,
         util.STATE_PATH, util.PROMPT_PATH, util.CONTEXT_PATH) = self._orig
        self._tmp.cleanup()

    def _instalacao_existente(self):
        """O config.toml é o carimbo de 'este app já rodou aqui' (config.load o cria)."""
        util.CONFIG_PATH.write_text("[detection]\n", encoding="utf-8")


class PadraoNeutroTest(_AppDirIsolado):
    def test_instalacao_nova_sai_sem_cabecalho(self):
        """O callout virou opt-in: quem enquadra a nota para a IA hoje é a moldura
        do context_prompt, que descarta o callout."""
        self.assertEqual(notes.default_context_note(), "")
        self.assertEqual(notes.load_context_note(), "")

    def test_texto_sugerido_existe_e_nao_fala_de_sap(self):
        texto = notes.suggested_context_note()
        self.assertIn("Contexto para IA", texto)
        self.assertNotIn("SAP", texto)
        self.assertNotIn("ABAP", texto)

    def test_texto_sugerido_sai_no_sabor_do_perfil_escolhido(self):
        """Quem respondeu o questionário recebe o cabeçalho DA ÁREA dele, não o
        genérico: o assistente guarda o perfil justamente para isto."""
        promptgen.save_profile(promptgen.Profile(base="abap"))
        self.assertIn("SAP/ABAP", notes.suggested_context_note())

    def test_perfil_guardado_sobrevive_ao_round_trip(self):
        p = promptgen.Profile(base="functional", role="Analista funcional", area="Fiscal")
        promptgen.save_profile(p)
        self.assertEqual(promptgen.load_profile(), p)

    def test_sem_perfil_guardado_nao_quebra(self):
        self.assertIsNone(promptgen.load_profile())

    def test_prompt_padrao_nao_fala_de_sap(self):
        texto = notes.default_summary_prompt()
        self.assertNotIn("SAP/ABAP", texto)
        self.assertNotIn("desenvolvedor ABAP", texto)
        # segue sendo um prompt válido pelo contrato com o leitor de notas
        self.assertEqual(promptgen.validate_prompt(texto), [])

    def test_arquivo_vazio_continua_significando_sem_cabecalho(self):
        util.CONTEXT_PATH.write_text("\n", encoding="utf-8")
        self.assertEqual(notes.load_context_note(), "")

    def test_o_texto_sap_continua_disponivel_para_o_perfil_abap(self):
        abap = promptgen.context_note_for(promptgen.Profile(base="abap"))
        self.assertIn("SAP/ABAP", abap)


class CongelamentoTest(_AppDirIsolado):
    def test_instalacao_nova_nao_e_tocada(self):
        notes.freeze_area_defaults()   # sem config.toml
        self.assertFalse(util.PROMPT_PATH.exists())
        self.assertFalse(util.CONTEXT_PATH.exists())
        self.assertEqual(notes.load_context_note(), "")

    def test_instalacao_existente_mantem_o_texto_sap(self):
        self._instalacao_existente()

        notes.freeze_area_defaults()

        self.assertIn("SAP/ABAP", util.CONTEXT_PATH.read_text(encoding="utf-8"))
        self.assertIn("SAP/ABAP", util.PROMPT_PATH.read_text(encoding="utf-8"))
        # o que a nota recebe continua sendo, byte a byte, o de antes da troca
        self.assertEqual(notes.load_context_note(), notes.AI_CONTEXT_NOTE)
        self.assertEqual(notes.load_summary_prompt(), notes.DEFAULT_SUMMARY_PROMPT.strip())

    def test_quem_nunca_escolheu_perfil_continua_na_fila_da_oferta(self):
        """O congelamento cria o prompt.md, que era a condição antiga da oferta."""
        self._instalacao_existente()

        notes.freeze_area_defaults()

        self.assertTrue(util.PROMPT_PATH.exists())
        self.assertTrue(promptgen.should_offer_on_boot())

    def test_quem_ja_tinha_prompt_nao_recebe_a_oferta(self):
        self._instalacao_existente()
        util.PROMPT_PATH.write_text("prompt que o usuario ja tinha\n", encoding="utf-8")

        notes.freeze_area_defaults()

        self.assertEqual(util.PROMPT_PATH.read_text(encoding="utf-8"),
                         "prompt que o usuario ja tinha\n")
        self.assertIn("SAP/ABAP", util.CONTEXT_PATH.read_text(encoding="utf-8"))
        self.assertFalse(promptgen.should_offer_on_boot())

    def test_rodar_de_novo_nao_sobrescreve_escolha_posterior(self):
        self._instalacao_existente()
        notes.freeze_area_defaults()
        util.CONTEXT_PATH.write_text("> **Contexto para IA:** o meu texto\n", encoding="utf-8")

        notes.freeze_area_defaults()

        self.assertEqual(notes.load_context_note(), "> **Contexto para IA:** o meu texto")


class OfertaDoAssistenteTest(_AppDirIsolado):
    def test_oferece_quando_ninguem_escolheu(self):
        self.assertTrue(promptgen.should_offer_on_boot())

    def test_nao_repete_depois_de_oferecida(self):
        promptgen.mark_profile_offered()
        self.assertFalse(promptgen.should_offer_on_boot())

    def test_nao_oferece_com_o_assistente_ja_concluido(self):
        promptgen.mark_wizard_done()
        self.assertFalse(promptgen.should_offer_on_boot())

    def test_flags_convivem_no_mesmo_state_json(self):
        promptgen.mark_profile_offered()
        promptgen.mark_wizard_done()
        self.assertTrue(promptgen.profile_offered())
        self.assertTrue(promptgen.wizard_done())


if __name__ == "__main__":
    unittest.main(verbosity=2)
