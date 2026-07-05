"""Sistema de temas Qt (#45): completude de tokens + contraste WCAG + troca/persistência.

Os testes de completude e contraste NÃO precisam de PySide6 (o theme.py só importa Qt
dentro de funções) — rodam sempre e são o portão de qualidade das cores: nenhum tema
entra sem todos os tokens preenchidos E com texto/fundo/acento legíveis (WCAG AA). O
teste de troca a quente exige PySide6 e pula com skip se ausente.
"""

import importlib.util
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from scriba.qt import theme  # noqa: E402  (não importa PySide6 no topo)

_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


# -- contraste WCAG 2.1 --------------------------------------------------------

def _linear(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hexstr: str) -> float:
    r, g, b = (int(hexstr[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


class CompletenessTests(unittest.TestCase):
    def test_todo_token_de_cor_e_hex_valido(self):
        for th in theme.themes():
            for name in theme.token_names():
                with self.subTest(theme=th.name, token=name):
                    self.assertRegex(getattr(th, name), _HEX)

    def test_slugs_unicos_e_defaults_registrados(self):
        names = [t.name for t in theme.themes()]
        self.assertEqual(len(names), len(set(names)), "slugs de tema duplicados")
        self.assertIn(theme.DEFAULT_DARK, names)
        self.assertIn(theme.DEFAULT_LIGHT, names)

    def test_ha_pelo_menos_um_claro_e_um_escuro(self):
        darks = [t for t in theme.themes() if t.dark]
        lights = [t for t in theme.themes() if not t.dark]
        self.assertTrue(darks and lights, "prova que o sistema flexiona fundo claro↔escuro")


class ContrastTests(unittest.TestCase):
    """Trava a legibilidade: cada par crítico precisa bater o mínimo WCAG. Se um tema
    novo (ou um ajuste de cor) rebaixar o contraste, isto quebra antes de chegar na tela."""

    def _pairs(self, t):
        # (rótulo, frente, fundo, mínimo). 4.5 = AA texto normal; 3.0 = AA texto grande/UI.
        return [
            ("text/bg", t.text, t.bg, 4.5),
            ("text/surface", t.text, t.surface, 4.5),
            ("text/field", t.text, t.field, 4.5),
            ("text/code_bg", t.text, t.code_bg, 4.5),
            ("muted/bg", t.muted, t.bg, 3.0),
            ("on_accent/accent", t.on_accent, t.accent, 4.5),
            ("on_highlight/highlight", t.on_highlight, t.highlight, 4.5),
            ("on_highlight/highlight_current", t.on_highlight, t.highlight_current, 4.0),
            ("selection", t.selection_fg, t.selection_bg, 4.5),
            ("ok/bg", t.ok, t.bg, 3.0),
            ("warn/bg", t.warn, t.bg, 3.0),
            ("rec/bg", t.rec, t.bg, 3.0),
            ("info/bg", t.info, t.bg, 3.0),
            ("code_err/code_bg", t.code_err, t.code_bg, 3.0),
        ]

    def test_contraste_minimo_por_tema(self):
        for th in theme.themes():
            for label, fg, bg, minimum in self._pairs(th):
                ratio = _contrast(fg, bg)
                with self.subTest(theme=th.name, pair=label):
                    self.assertGreaterEqual(
                        ratio, minimum, f"{th.name} {label} = {ratio:.2f} (< {minimum})"
                    )


class QssTests(unittest.TestCase):
    def test_qss_usa_as_cores_do_tema(self):
        for th in theme.themes():
            css = theme.qss(th)
            for token in ("bg", "text", "accent", "field", "border"):
                with self.subTest(theme=th.name, token=token):
                    self.assertIn(getattr(th, token), css)


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class SwitchTests(unittest.TestCase):
    def setUp(self):
        self._orig_path = __import__("scriba.util", fromlist=["util"]).STATE_PATH
        self._orig_active = theme._active
        from scriba import util
        util.STATE_PATH = Path(tempfile.mkdtemp(prefix="scriba_theme_")) / "state.json"
        theme._active = None  # limpa o cache p/ reler do state temporário

    def tearDown(self):
        from scriba import util
        util.STATE_PATH = self._orig_path
        theme._active = self._orig_active

    def test_troca_persiste_e_muda_o_ativo(self):
        # sem QApplication, set_active só cacheia + persiste (não reaplica QSS)
        theme.set_active("claude")
        self.assertEqual(theme.active().name, "claude")
        from scriba import util
        self.assertEqual(util.read_state().get("ui_theme"), "claude")

    def test_nome_invalido_cai_no_default_do_so(self):
        theme.set_active("naoexiste")
        self.assertEqual(theme.active().name, theme.os_default_theme())

    def test_active_sem_escolha_segue_o_so(self):
        # state temporário vazio (setUp) + cache limpo -> deriva do SO
        theme._active = None
        self.assertEqual(theme.active().name, theme.os_default_theme())

    def test_clear_choice_volta_ao_automatico(self):
        from scriba import util

        theme.set_active("claude")
        theme.clear_choice()
        self.assertNotIn("ui_theme", util.read_state())
        self.assertEqual(theme.active().name, theme.os_default_theme())

    def test_current_choice_reflete_escolha_explicita(self):
        self.assertIsNone(theme.current_choice())      # state temporário vazio = automático
        theme.set_active("sublime")
        self.assertEqual(theme.current_choice(), "sublime")
        theme.clear_choice()
        self.assertIsNone(theme.current_choice())

    def test_apply_choice_slug_persiste_e_ativa(self):
        theme.apply_choice("light")                    # sem QApplication: só cacheia + persiste
        self.assertEqual(theme.current_choice(), "light")
        self.assertEqual(theme.active().name, "light")

    def test_apply_choice_none_volta_ao_automatico(self):
        theme.set_active("claude")
        theme.apply_choice(None)                        # None = automático (segue o SO)
        self.assertIsNone(theme.current_choice())
        self.assertEqual(theme.active().name, theme.os_default_theme())


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class RestyleContractTests(unittest.TestCase):
    """Contrato da troca a quente (#70): theme.apply empurra restyle_theme p/ as janelas
    abertas que o expõem, e NÃO quebra com as que não têm (nem se uma restyle falhar)."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_apply_chama_restyle_e_ignora_quem_nao_tem(self):
        from PySide6.QtWidgets import QWidget

        calls = []

        class _W(QWidget):
            def restyle_theme(inner):
                calls.append(1)

        w = _W(); w.show()
        plain = QWidget(); plain.show()          # sem restyle_theme: não pode quebrar
        orig = theme._active
        self.addCleanup(lambda: setattr(theme, "_active", orig))
        self.addCleanup(w.close)
        self.addCleanup(plain.close)
        theme.apply(self.app, theme.by_slug("vscode"))
        self.assertTrue(calls)                   # a janela foi re-estilizada

    def test_uma_restyle_que_falha_nao_derruba_as_outras(self):
        from PySide6.QtWidgets import QWidget

        ok = []

        class _Bad(QWidget):
            def restyle_theme(inner):
                raise RuntimeError("boom no restyle")

        class _Good(QWidget):
            def restyle_theme(inner):
                ok.append(1)

        bad = _Bad(); bad.show()
        good = _Good(); good.show()
        self.addCleanup(bad.close)
        self.addCleanup(good.close)
        theme._restyle_top_levels(self.app)      # não propaga a exceção
        self.assertTrue(ok)                      # a boa ainda rodou


# -- ícones Fluent vendorizados (#59) -----------------------------------------

# Vocabulário que a fundação promete (scriba/qt/icons/*.svg). Se um nome sumir do
# repo ou parar de recolorir, isto quebra antes de a tela ficar sem ícone.
_ICON_VOCAB = [
    "copy", "chat", "people", "delete", "more-horizontal", "chevron-up",
    "chevron-down", "chevron-right", "search", "folder", "refresh", "checkmark",
    "warning", "hourglass", "settings", "arrow-upload", "stop", "record", "cut",
    "dismiss", "play", "edit", "sparkle", "task-list",
]


class IconTests(unittest.TestCase):
    def test_vocabulario_resolve_e_recolore(self):
        for name in _ICON_VOCAB:
            with self.subTest(icon=name):
                p = theme.icon(name, "#abcdef")
                self.assertTrue(p and Path(p).exists(), f"ícone {name} não gerou arquivo")
                txt = Path(p).read_text(encoding="utf-8")
                self.assertIn("#abcdef", txt, "a cor do tema não entrou no SVG")
                self.assertNotIn("#212121", txt, "sobrou a cor-fonte do Fluent")

    def test_icone_inexistente_falha_graciosa(self):
        self.assertEqual(theme.icon("nao-existe-xyz"), "")

    def test_svgs_e_licenca_vendorizados(self):
        d = Path(theme.__file__).resolve().parent / "icons"
        self.assertTrue((d / "LICENSE").exists(), "faltou a licença MIT do Fluent")
        for name in _ICON_VOCAB:
            with self.subTest(icon=name):
                self.assertTrue((d / f"{name}.svg").exists(), f"SVG {name} não vendorizado")

    @unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
    def test_qicon_nao_vazio(self):
        self.assertFalse(theme.qicon("chevron-down").isNull())
        self.assertTrue(theme.qicon("nao-existe-xyz").isNull())


class OsDefaultTests(unittest.TestCase):
    def test_os_default_e_um_tema_registrado(self):
        self.assertIn(theme.os_default_theme(), [t.name for t in theme.themes()])

    def test_os_default_e_escuro_ou_claro(self):
        self.assertIn(theme.os_default_theme(), (theme.DEFAULT_DARK, theme.DEFAULT_LIGHT))


if __name__ == "__main__":
    unittest.main(verbosity=2)
