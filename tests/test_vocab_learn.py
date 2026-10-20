"""Vocabulário que aprende com as reuniões (#227): TERMOS do resumo -> candidatos ->
sugestões (2+ reuniões) -> aceitos/recusados pelo usuário -> uso na correção, no
resumo e no Whisper. Nada entra no vocabulário sem o usuário aceitar."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from scriba import util, vocab_learn  # noqa: E402


class _AppDir(unittest.TestCase):
    def setUp(self):
        self._orig = (util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH)
        d = Path(tempfile.mkdtemp(prefix="scriba_learn_"))
        util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH = d, d / "logs", d / "config.toml"

    def tearDown(self):
        util.APP_DIR, util.LOGS_DIR, util.CONFIG_PATH = self._orig


class ParseTermsLineTests(unittest.TestCase):
    def test_virgulas_vazios_e_nenhum(self):
        self.assertEqual(vocab_learn.parse_terms_line(" ZPL, `SO10`; React , ZPL"), ["ZPL", "SO10", "React"])
        self.assertEqual(vocab_learn.parse_terms_line(" -"), [])
        self.assertEqual(vocab_learn.parse_terms_line("nenhum"), [])


class AprendizadoTests(_AppDir):
    def test_vira_sugestao_na_segunda_reuniao(self):
        self.assertEqual(vocab_learn.record("r1", ["ZPL", "Kubernetes"], ""), [])
        self.assertEqual(vocab_learn.suggestions(), [])
        self.assertEqual(vocab_learn.record("r1", ["ZPL"], ""), [])   # mesma reunião não conta 2x
        self.assertEqual(vocab_learn.record("r2", ["ZPL", "Kafka"], ""), ["ZPL"])
        self.assertEqual([s["term"] for s in vocab_learn.suggestions()], ["ZPL"])
        self.assertEqual(vocab_learn.record("r3", ["ZPL"], ""), [])   # já era sugestão: sem novo aviso

    def test_so_identificadores_tecnicos_viram_candidatos(self):
        """Medido com o Claude real: além de ZPL/SO10 vinham "etiqueta", "label size",
        "impressora Zebra", "SmartForms XML download" - palavra comum e frase não servem."""
        for r in ("r1", "r2"):
            vocab_learn.record(r, ["ZPL", "SO10", "base64", "DPI", "React Native", "etiqueta", "label size",
                                   "impressora Zebra", "programa ABAP", "SmartForms XML download"], "")
        self.assertEqual(sorted(s["term"] for s in vocab_learn.suggestions()),
                         ["DPI", "React Native", "SO10", "ZPL", "base64"])

    def test_termo_ja_no_vocabulario_e_ignorado(self):
        vocab_learn.record("r1", ["SE80", "ZPL"], "SAP ABAP SE80")
        vocab_learn.record("r2", ["SE80", "ZPL"], "SAP ABAP SE80")
        self.assertEqual([s["term"] for s in vocab_learn.suggestions()], ["ZPL"])

    def test_aceitar_e_recusar(self):
        for r in ("r1", "r2"):
            vocab_learn.record(r, ["ZPL", "Terraform"], "")
        vocab_learn.set_status("ZPL", vocab_learn.ACEITO)
        vocab_learn.set_status("Terraform", vocab_learn.RECUSADO)
        self.assertEqual(vocab_learn.suggestions(), [])
        self.assertEqual(vocab_learn.accepted(), ["ZPL"])
        vocab_learn.record("r3", ["Terraform"], "")   # recusado não volta a ser sugerido
        self.assertEqual(vocab_learn.suggestions(), [])

    def test_aceitos_somam_ao_vocabulario_sem_mexer_no_digitado(self):
        for r in ("r1", "r2"):
            vocab_learn.record(r, ["ZPL", "React Native"], "")
        vocab_learn.set_status("ZPL", vocab_learn.ACEITO)
        vocab_learn.set_status("React Native", vocab_learn.ACEITO)
        self.assertEqual(vocab_learn.all_terms("SAP ABAP"), ["SAP", "ABAP", "React Native", "ZPL"])
        self.assertEqual(vocab_learn.whisper_hotwords("SAP ABAP"), "SAP ABAP React Native ZPL")
        self.assertEqual(vocab_learn.whisper_hotwords(""), "React Native ZPL")

    def test_arquivo_corrompido_nao_derruba(self):
        (util.APP_DIR / "vocab_learned.json").write_text("{quebrado", encoding="utf-8")
        self.assertEqual(vocab_learn.suggestions(), [])
        self.assertEqual(vocab_learn.record("r1", ["ZPL"], ""), [])


class CabecalhoDoResumoTests(unittest.TestCase):
    def test_linha_termos_e_consumida_e_devolvida(self):
        from scriba.notes import split_header, split_header_terms

        texto = "TITULO: Estimativa etiqueta\nCLIENTE: ?\nTERMOS: ZPL, SmartForms, SO10\n\n## Objetivo\ncorpo"
        body, title, client, terms = split_header_terms(texto)
        self.assertEqual((title, client, terms), ("Estimativa etiqueta", None, ["ZPL", "SmartForms", "SO10"]))
        self.assertNotIn("TERMOS", body)
        self.assertEqual(split_header(texto)[1:], ("Estimativa etiqueta", None))

    def test_sem_termos(self):
        from scriba.notes import split_header_terms

        self.assertEqual(split_header_terms("TITULO: X\nCLIENTE: Y\nTERMOS: -\ncorpo")[3], [])
        self.assertEqual(split_header_terms("TITULO: X\nCLIENTE: Y\ncorpo")[3], [])

    def test_instrucao_pede_termos_genericos_nao_so_sap(self):
        from scriba.notes import TITLE_INSTRUCTION

        self.assertIn("TERMOS:", TITLE_INSTRUCTION)
        self.assertIn("bibliotecas", TITLE_INSTRUCTION)


class ResumoAprendeTests(_AppDir):
    def test_termos_do_resumo_viram_sugestao_e_vao_para_o_meta(self):
        from scriba import notes

        resposta = "TITULO: T\nCLIENTE: ?\nTERMOS: ZPL, Labelary\n\n## Objetivo\ncorpo"
        pastas = []
        for i in (1, 2):
            pasta = util.APP_DIR / f"r{i}"
            pasta.mkdir()
            (pasta / "meta.json").write_text(json.dumps({"started_at": f"2026-10-2{i}T10:00:00"}), encoding="utf-8")
            pastas.append(pasta)
        with mock.patch("scriba.ai.complete", return_value=resposta), mock.patch("builtins.print"):
            for pasta in pastas:
                body, title, _ = notes.generate_summary("[00:00] Eu: oi", pasta)
        self.assertEqual(title, "T")
        self.assertNotIn("TERMOS", body)
        meta2 = json.loads((pastas[1] / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta2["vocab_suggested"], ["ZPL", "Labelary"])
        self.assertNotIn("vocab_suggested", json.loads((pastas[0] / "meta.json").read_text(encoding="utf-8")))


@unittest.skipUnless(__import__("importlib").util.find_spec("PySide6"), "PySide6 ausente")
class TelaTests(_AppDir):
    def test_adicionar_pela_tela(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication

        from scriba import config as config_mod
        from scriba.qt import widgets
        from scriba.qt.settings_ui import SettingsWindow

        QApplication.instance() or QApplication([])
        util.CONFIG_PATH.write_text(config_mod.DEFAULT_CONFIG, encoding="utf-8")
        for r in ("r1", "r2"):
            vocab_learn.record(r, ["ZPL"], "")

        class _App:
            cfg = config_mod.load()

            def reload_config(self):
                self.cfg = config_mod.load()

        with mock.patch.object(util, "PROMPT_PATH", util.APP_DIR / "prompt.md"), \
                mock.patch.object(util, "CONTEXT_PATH", util.APP_DIR / "context.md"), \
                mock.patch.object(util, "STATE_PATH", util.APP_DIR / "state.json"):
            win = SettingsWindow(_App())
            botoes = [b for b in win.findChildren(widgets.ModernButton) if b.text() == "Adicionar"]
            self.assertEqual(len(botoes), 1)
            botoes[0].click()
            self.assertEqual(vocab_learn.accepted(), ["ZPL"])
            QApplication.sendPostedEvents(None, QEvent.DeferredDelete)   # a lista foi refeita
            textos = [b.text() for b in win.findChildren(widgets.ModernButton)]
            self.assertNotIn("Adicionar", textos)
            self.assertIn("Remover", textos)
            win.close()


if __name__ == "__main__":
    unittest.main()
