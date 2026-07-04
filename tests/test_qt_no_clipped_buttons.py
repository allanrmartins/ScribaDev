"""#64: guarda de regressão de layout - NENHUM botão pode renderizar cortado.

O bug do botão primário da command bar de Notas (renderizado cortado dos dois lados)
passou pelo smoke E2E porque ninguém aferia largura-real vs a largura necessária. Este
teste varre as janelas grandes em tamanhos apertados (min + default, e cada aba do
Settings) e falha se algum botão VISÍVEL tem width < minimumSizeHint (o mínimo p/ caber
o texto/ícone). Função reusável (_sweep) para novas janelas entrarem fácil.
Exige PySide6 (pula sem o extra 'qt')."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None


# tolerância p/ o minimumSizeHint de QPushButton estilizado, que às vezes excede em 1-3px
# a largura natural (arredondamento/estilo). Cortes REAIS (o que este guard caça) são de
# DEZENAS de px, então uma folga pequena não os mascara.
_TOL = 4


def _clipped_now(window) -> list[str]:
    """Botões cortados no estado ATUAL da janela (tamanho/aba correntes)."""
    from PySide6.QtWidgets import QAbstractButton

    out = []
    for b in window.findChildren(QAbstractButton):
        if not b.isVisible() or b.width() == 0:
            continue
        need = b.minimumSizeHint().width()
        if need > 0 and b.width() + _TOL < need:
            out.append(f"{type(b).__name__} {b.text()!r} width={b.width()} < min={need}")
    return out


def _sweep(window, sizes, tabs=None) -> list[str]:
    from PySide6.QtWidgets import QApplication

    bad = []
    window.show()
    for w, h in sizes:
        window.resize(w, h)
        QApplication.processEvents()
        idxs = range(tabs.count()) if tabs is not None else [None]
        for ti in idxs:
            if ti is not None:
                tabs.setCurrentIndex(ti)
                QApplication.processEvents()
            tag = f"{w}x{h}" + (f"/aba{ti}" if ti is not None else "")
            bad += [f"[{tag}] {m}" for m in _clipped_now(window)]
    window.hide()
    return bad


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class NoClippedButtonsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        from scriba.qt import theme

        cls.app = QApplication.instance() or QApplication([])
        theme.apply(cls.app)   # o QSS influencia a largura mínima dos botões

    def setUp(self):
        # CONFIG_PATH temporário: Settings/Chat leem config.load(); não tocar o real
        from scriba import config as config_mod, util

        self._d = Path(tempfile.mkdtemp(prefix="scriba_clip_"))
        self._orig_cfg = util.CONFIG_PATH
        self._orig_prompt = util.PROMPT_PATH
        util.CONFIG_PATH = self._d / "config.toml"
        util.CONFIG_PATH.write_text(config_mod.DEFAULT_CONFIG, encoding="utf-8")
        util.PROMPT_PATH = self._d / "prompt.md"

    def tearDown(self):
        from scriba import util

        util.CONFIG_PATH = self._orig_cfg
        util.PROMPT_PATH = self._orig_prompt

    def test_notas_sem_botao_cortado(self):
        from scriba.qt.notes_ui import NotesWindow

        base = self._d / "notas"
        base.mkdir()
        (base / "2026-07-02_15-30_x.md").write_text(
            "---\ntitulo: Boleto não gera em produção com CNPJ alfanumérico\n"
            "data: 2026-07-02T15:30:00\ncliente: ACME\n---\n\n## Objetivo\nX.\n\n"
            "## Transcrição completa\nfala\n", encoding="utf-8")

        class _Out:
            def resolved_export_dir(_s): return base
            def resolved_recordings_dir(_s): return base / "_rec"

        class _App:
            cfg = type("C", (), {"output": _Out()})()
            def ui(_s, fn): fn()

        win = NotesWindow(_App())
        win._refresh_list()
        for it in win._items:                 # seleciona a nota -> command bar ativa
            win._tree.setCurrentItem(it)
            break
        bad = _sweep(win, [(880, 560), (1040, 680)])
        self.assertEqual(bad, [], "botões cortados na tela de Notas: " + "; ".join(bad))

    def test_chat_sem_botao_cortado(self):
        from scriba.qt.chat_ui import ChatWindow

        win = ChatWindow("## Resumo\n- item um\n- item dois", "fala 1\nfala 2", "Reunião de teste")
        bad = _sweep(win, [(560, 460), (640, 620)])
        self.assertEqual(bad, [], "botões cortados no Chat: " + "; ".join(bad))

    def test_log_sem_botao_cortado(self):
        from scriba.qt.log_ui import LogWindow

        class _App:
            cfg = type("C", (), {"output": type("O", (), {
                "resolved_recordings_dir": staticmethod(lambda: None)})()})()

        win = LogWindow(_App())
        bad = _sweep(win, [(640, 380), (960, 600)])
        self.assertEqual(bad, [], "botões cortados no Log: " + "; ".join(bad))

    def test_capa_sem_botao_cortado(self):
        from scriba.qt.main_window import MainWindow

        class _App:
            call_active = False
            update_news = None
            def is_recording(self): return False
            def current_call_app(self): return "Teams"
            def recording_duration(self): return 72.0
            def call_duration(self): return 30.0
            def show_settings(self): pass
            def show_notes(self): pass
            def show_log(self): pass
            def start_recording(self, *a): pass
            def stop_recording(self, **k): pass
            def ui(self, fn): fn()

        win = MainWindow(_App())
        win.show_update("0.9.0")   # mostra a barra de update (com o botão)
        bad = _sweep(win, [(520, 560), (560, 620)])
        self.assertEqual(bad, [], "botões cortados na capa: " + "; ".join(bad))

    def test_configuracoes_sem_botao_cortado(self):
        from PySide6.QtWidgets import QTabWidget

        from scriba import config as config_mod
        from scriba.qt.settings_ui import SettingsWindow

        class _App:
            cfg = config_mod.load()
            def reload_config(self): self.cfg = config_mod.load()
            def apply_update(self): pass
            def relaunch(self): pass

        win = SettingsWindow(_App())
        tabs = win.findChild(QTabWidget)
        bad = _sweep(win, [(720, 480), (860, 640)], tabs=tabs)
        self.assertEqual(bad, [], "botões cortados em Configurações: " + "; ".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
