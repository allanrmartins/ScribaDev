"""Testes de scriba.notes.split_header — tolerância a preâmbulo do modelo (issue #18).

Roda sem dependências externas:  python -m unittest discover -s tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import notes, speakers, util  # noqa: E402
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


class RelabelSpeakersTests(unittest.TestCase):
    """notes.relabel_speakers (#1): aprende a voz E corrige a nota já gerada."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="scriba_relabel_"))
        # store de voz ISOLADO — nunca tocar no speakers.json real do usuário
        util.APP_DIR = self.d / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        speakers.STORE_PATH = util.APP_DIR / "speakers.json"
        self.rec = self.d / "rec"
        self.rec.mkdir(parents=True)
        self.export = self.d / "2026-06-10_20-00_reuniao.md"
        (self.rec / "voices.json").write_text(json.dumps({
            "Participante 1": {"embedding": [1.0, 0.0, 0.0], "auto": False, "score": 0.0},
            "Participante 2": {"embedding": [0.0, 1.0, 0.0], "auto": False, "score": 0.0},
        }), encoding="utf-8")
        (self.rec / "transcript.json").write_text(json.dumps([
            {"start": 0.0, "end": 1.0, "speaker": "Eu", "text": "oi"},
            {"start": 1.0, "end": 2.0, "speaker": "Participante 2", "text": "aqui e o Marcelo"},
        ]), encoding="utf-8")
        nota = ("# Reuniao\n\n**[00:00:01] Participante 2:** aqui e o Marcelo\n\n"
                "## Participantes\n- Participante 2 (voz)\n")
        (self.rec / "notas.md").write_text(nota, encoding="utf-8")
        self.export.write_text(nota, encoding="utf-8")
        (self.rec / "meta.json").write_text(
            json.dumps({"export_path": str(self.export)}), encoding="utf-8")

    def test_aprende_e_corrige_nota_e_transcricao(self):
        ok = notes.relabel_speakers(self.rec, {"Participante 2": "Marcelo"})
        self.assertTrue(ok)
        # transcript.json: campo speaker trocado
        turns = json.loads((self.rec / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(turns[1]["speaker"], "Marcelo")
        # markdown local E exportado: trocado, sem sobra de "Participante 2"
        for md in (self.rec / "notas.md", self.export):
            txt = md.read_text(encoding="utf-8")
            self.assertIn("Marcelo:", txt)
            self.assertNotIn("Participante 2", txt)
        # store global: aprendeu Marcelo com o embedding da voz 2
        st = speakers.load_store()
        self.assertEqual([e["name"] for e in st], ["Marcelo"])
        self.assertEqual(st[0]["embedding"], [0.0, 1.0, 0.0])
        # voices.json: chave renomeada e marcada como rotulada à mão
        voices = json.loads((self.rec / "voices.json").read_text(encoding="utf-8"))
        self.assertIn("Marcelo", voices)
        self.assertNotIn("Participante 2", voices)
        self.assertTrue(voices["Marcelo"]["labeled"])

    def test_sem_mudanca_e_noop(self):
        self.assertFalse(notes.relabel_speakers(self.rec, {"Participante 1": ""}))
        self.assertFalse(notes.relabel_speakers(self.rec, {"Participante 1": "Participante 1"}))
        self.assertEqual(speakers.load_store(), [])

    def test_word_boundary_nao_pega_participante_12(self):
        (self.rec / "notas.md").write_text("Participante 1 e Participante 12 aqui", encoding="utf-8")
        notes.relabel_speakers(self.rec, {"Participante 1": "Ana"})
        txt = (self.rec / "notas.md").read_text(encoding="utf-8")
        self.assertIn("Ana e Participante 12 aqui", txt)


class ParseParticipantsTests(unittest.TestCase):
    """notes.parse_participants: extrai a seção Participantes do resumo."""

    _MD = (
        "# Reunião X\n\n## Objetivo\nbla\n\n## Participantes\n\n"
        "**Presentes:**\n"
        "- **Eu** — desenvolvedor ABAP\n"
        "- **Participante 1** — conduz a reunião; nome não estabelecido com segurança\n"
        "- **Participante 2** — Alex (identificado: responde quando chamam 'Alex')\n\n"
        "**Mencionados (não necessariamente presentes):**\n"
        "- **Renato** — cliente\n"
        "- **Ebert / Herbert** — interno\n\n"
        "## Transcrição completa\n\n**[00:00:00] Eu:** oi\n"
    )

    def test_presentes_e_mencionados(self):
        pres, menc = notes.parse_participants(self._MD)
        self.assertIn("Eu", pres)
        self.assertTrue(pres["Participante 2"].startswith("Alex ("))
        self.assertEqual(menc, ["Renato", "Ebert / Herbert"])
        self.assertNotIn("Renato", pres)

    def test_sem_secao_participantes(self):
        self.assertEqual(notes.parse_participants("# Nota\n\n## Resumo\ntexto"), ({}, []))


class GuessVoiceNameTests(unittest.TestCase):
    """notes.guess_voice_name: palpite de nome a partir da descrição da IA."""

    def test_ja_nomeada_retorna_o_proprio_label(self):
        self.assertEqual(notes.guess_voice_name("Pedro", "papel técnico"), "Pedro")

    def test_nome_no_inicio_entre_parenteses(self):
        self.assertEqual(notes.guess_voice_name("Participante 2", "Alex (identificado: …)"), "Alex")

    def test_marcador_possivelmente(self):
        self.assertEqual(
            notes.guess_voice_name("Participante 5", "papel técnico; possivelmente Paulo, mas citado em 3a pessoa"),
            "Paulo")

    def test_sem_palpite_vira_vazio(self):
        self.assertEqual(
            notes.guess_voice_name("Participante 1", "conduz a reunião; nome não estabelecido com segurança"), "")

    def test_nome_entre_aspas(self):
        self.assertEqual(
            notes.guess_voice_name("Participante 1", 'coordena a call; possivelmente "Suzy" (chamada pelo nome)'),
            "Suzy")

    def test_nome_entre_aspas_curvas(self):
        self.assertEqual(
            notes.guess_voice_name("Participante 1", "coordena; possivelmente “Suzy” (chamada)"), "Suzy")

    def test_nome_com_barra_pega_o_primeiro(self):
        self.assertEqual(
            notes.guess_voice_name("Participante 3", 'analista; possivelmente "Marcão/Marco" (chamado e responde)'),
            "Marcão")

    def test_marcador_com_artigo(self):
        self.assertEqual(
            notes.guess_voice_name("Participante 2", "conduz; provavelmente o 'Pedro' citado"), "Pedro")


class DateMaskTests(unittest.TestCase):
    """util.format_date_br + date_br_to_iso: máscara e validação dos filtros de data."""

    def test_format_acumulando_digitos(self):
        # formata corretamente em qualquer ponto da digitação
        self.assertEqual(util.format_date_br("1"), "1")
        self.assertEqual(util.format_date_br("19"), "19")
        self.assertEqual(util.format_date_br("190"), "19/0")
        self.assertEqual(util.format_date_br("1902"), "19/02")
        self.assertEqual(util.format_date_br("190219"), "19/02/19")
        self.assertEqual(util.format_date_br("19021988"), "19/02/1988")

    def test_format_descarta_nao_digito_e_limita_8(self):
        self.assertEqual(util.format_date_br("aqsas"), "")
        self.assertEqual(util.format_date_br("19/02/19x88"), "19/02/1988")
        self.assertEqual(util.format_date_br("1902198899"), "19/02/1988")  # cap em 8

    def test_iso_de_data_valida(self):
        self.assertEqual(util.date_br_to_iso("19/02/1988"), "1988-02-19")
        self.assertEqual(util.date_br_to_iso(" 01/12/2026 "), "2026-12-01")

    def test_iso_parcial_ou_invalida_vira_vazio(self):
        for s in ("", "19/02", "31/02/2020", "99/99/9999", "19/21/8890", "abc", "1988-02-19"):
            self.assertEqual(util.date_br_to_iso(s), "", s)


class DateRangeFilterTests(unittest.TestCase):
    """util.date_range_filter: só DE = aquele dia; DE+ATÉ = intervalo (ordem livre)."""

    def test_so_de_e_aquele_dia(self):
        self.assertEqual(util.date_range_filter("10/06/2026", ""), ("2026-06-10", "2026-06-10"))

    def test_so_ate_e_aquele_dia(self):
        self.assertEqual(util.date_range_filter("", "10/06/2026"), ("2026-06-10", "2026-06-10"))

    def test_de_e_ate_viram_intervalo(self):
        self.assertEqual(util.date_range_filter("10/06/2026", "12/06/2026"),
                         ("2026-06-10", "2026-06-12"))

    def test_ordem_invertida_ajusta_min_max(self):
        self.assertEqual(util.date_range_filter("12/06/2026", "10/06/2026"),
                         ("2026-06-10", "2026-06-12"))

    def test_vazio_ou_invalido_nao_filtra(self):
        self.assertEqual(util.date_range_filter("", ""), (None, None))
        self.assertEqual(util.date_range_filter("19/02", "abc"), (None, None))

    def test_um_valido_outro_parcial_usa_o_valido_como_dia(self):
        self.assertEqual(util.date_range_filter("10/06/2026", "12/06"), ("2026-06-10", "2026-06-10"))
        self.assertEqual(util.date_range_filter("xx", "12/06/2026"), ("2026-06-12", "2026-06-12"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
