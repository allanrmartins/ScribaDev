"""#228: aviso de atualização por sistema e "O que há de novo" após atualizar."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from scriba import novidades, updates, util  # noqa: E402

MANIFESTO = {"version": "1.4.27", "url": "u", "notes": "geral",
             "version_mac": "1.4.28", "notes_mac": "só mac"}


class ManifestoPorSistemaTests(unittest.TestCase):
    def test_cada_sistema_ve_a_sua_versao(self):
        self.assertEqual(updates._for_this_os(MANIFESTO, "darwin")["version"], "1.4.28")
        self.assertEqual(updates._for_this_os(MANIFESTO, "darwin")["notes"], "só mac")
        self.assertEqual(updates._for_this_os(MANIFESTO, "win32")["version"], "1.4.27")   # sem version_win
        self.assertEqual(updates._for_this_os(MANIFESTO, "linux")["version"], "1.4.27")

    def test_campo_do_sistema_invalido_cai_no_geral(self):
        m = dict(MANIFESTO, version_mac="lixo")
        self.assertEqual(updates._for_this_os(m, "darwin")["version"], "1.4.27")

    def test_release_so_de_mac_nao_avisa_o_windows(self):
        m = {"version": "1.4.26", "version_mac": "1.4.27"}
        with mock.patch.object(updates, "_http_json", return_value=m), \
                mock.patch.object(updates, "__version__", "1.4.26"):
            with mock.patch.object(sys, "platform", "win32"):
                self.assertIsNone(updates.update_available())
            with mock.patch.object(sys, "platform", "darwin"):
                self.assertEqual(updates.update_available(), "1.4.27")


FAKE = [
    {"versao": "1.4.28", "destaques": [], "melhorias": [{"texto": "só win", "so": ("win",)}], "correcoes": []},
    {"versao": "1.4.27", "destaques": [{"titulo": "Mac", "texto": "x", "so": ("mac",)}],
     "melhorias": [{"texto": "todos", "so": ("mac", "win")}], "correcoes": []},
    {"versao": "1.4.26", "destaques": [], "melhorias": [{"texto": "antiga", "so": ("mac", "win")}],
     "correcoes": []},
]


class PendentesTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(novidades, "NOVIDADES", FAKE)
        p.start()
        self.addCleanup(p.stop)

    def test_junta_as_versoes_nao_vistas_mais_nova_primeiro(self):
        vs = novidades.pendentes("1.4.28", "1.4.26", platform="win32")
        self.assertEqual([v["versao"] for v in vs], ["1.4.28", "1.4.27"])

    def test_filtra_pelo_sistema_e_some_com_versao_vazia(self):
        mac = novidades.pendentes("1.4.28", "1.4.26", platform="darwin")
        self.assertEqual([v["versao"] for v in mac], ["1.4.27"])   # a 1.4.28 só tinha item de Windows
        self.assertEqual(mac[0]["destaques"][0]["titulo"], "Mac")
        win = novidades.pendentes("1.4.27", "1.4.26", platform="win32")
        self.assertEqual(win[0]["destaques"], [])

    def test_sem_versao_vista_mostra_tudo_ate_a_atual(self):
        self.assertEqual([v["versao"] for v in novidades.pendentes("1.4.27", None, platform="win32")],
                         ["1.4.27", "1.4.26"])



class DadosReaisTests(unittest.TestCase):
    def test_bem_formados_e_texto_de_mac_so_no_mac(self):
        """Cada item diz o sistema; item que vale p/ o Windows não fala em Dock (achado na
        revisão do desenho: "minimizadas no Dock" aparecia p/ quem usa Windows)."""
        for entrada in novidades.NOVIDADES:
            for d in entrada["destaques"]:
                self.assertTrue(d["titulo"] and d["texto"] and d["so"])
            for secao in ("destaques", "melhorias", "correcoes"):
                for item in entrada[secao]:
                    self.assertTrue(set(item["so"]) <= {"mac", "win"})
                    if "win" in item["so"]:
                        self.assertNotIn("Dock", item.get("texto", ""))


class AberturaTests(unittest.TestCase):
    """ScribaApp via __new__ (sem __init__): nada de Qt real."""

    def setUp(self):
        self._orig = (util.APP_DIR, util.STATE_PATH)
        d = Path(tempfile.mkdtemp(prefix="scriba_nov_"))
        util.APP_DIR, util.STATE_PATH = d, d / "state.json"
        p = mock.patch.object(novidades, "NOVIDADES", FAKE)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        util.APP_DIR, util.STATE_PATH = self._orig

    def _app(self, gravando=False):
        from scriba.main import ScribaApp

        app = ScribaApp.__new__(ScribaApp)
        app.call_active = False
        app.is_recording = lambda: gravando
        app._after = mock.Mock()
        app._open_novidades_ui = mock.Mock()
        return app

    def test_depois_de_atualizar_abre_uma_vez(self):
        util.update_state(setup_wizard_done=True, novidades_vistas="1.4.26")
        app = self._app()
        with mock.patch("scriba.__version__", "1.4.27"):
            app._check_novidades_boot()
            app._check_novidades_boot()
        app._open_novidades_ui.assert_called_once()
        self.assertEqual(util.read_state()["novidades_vistas"], "1.4.27")

    def test_instalacao_nova_nao_mostra(self):
        app = self._app()
        with mock.patch("scriba.__version__", "1.4.27"):
            app._check_novidades_boot()
        app._open_novidades_ui.assert_not_called()
        self.assertEqual(util.read_state()["novidades_vistas"], "1.4.27")

    def test_nunca_por_cima_de_gravacao(self):
        util.update_state(setup_wizard_done=True, novidades_vistas="1.4.26")
        app = self._app(gravando=True)
        with mock.patch("scriba.__version__", "1.4.27"):
            app._check_novidades_boot()
        app._open_novidades_ui.assert_not_called()
        app._after.assert_called_once()   # tenta de novo depois
        self.assertEqual(util.read_state()["novidades_vistas"], "1.4.26")


@unittest.skipUnless(__import__("importlib").util.find_spec("PySide6"), "PySide6 ausente")
class TelaTests(unittest.TestCase):
    def test_experimentar_abre_a_aba_do_destaque(self):
        from PySide6.QtWidgets import QApplication

        from scriba.qt import widgets
        from scriba.qt.novidades_ui import NovidadesWindow

        QApplication.instance() or QApplication([])
        abertas = []
        versoes = novidades.pendentes("1.4.26", None, platform="darwin")
        w = NovidadesWindow(versoes, on_abrir=abertas.append)
        botoes = [b for b in w.findChildren(widgets.ModernButton) if b.text() == "Experimentar"]
        self.assertEqual(len(botoes), len(versoes[0]["destaques"]))
        botoes[0].click()
        self.assertEqual(abertas, ["Transcrição"])
        w.close()


if __name__ == "__main__":
    unittest.main()
