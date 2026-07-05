"""Capa Qt (#47): smoke offscreen do render de status, tick, barra de update e X->esconde."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None


class _FakeApp:
    def __init__(self, recording=False, call_active=False):
        self._rec = recording
        self.call_active = call_active
        self.update_news = None
        self.cfg = None            # sem app real: _collect_home não toca o índice
        self.notes_opened = "unset"

    def is_recording(self): return self._rec
    def current_call_app(self): return "Teams"
    def recording_duration(self): return 72.0
    def call_duration(self): return 30.0
    def show_settings(self): pass
    def show_notes(self, note_path=None): self.notes_opened = note_path
    def show_log(self): pass
    def start_recording(self, *a): pass
    def stop_recording(self, **k): pass
    def ui(self, fn): fn()


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _win(self, **kw):
        from scriba.qt.main_window import MainWindow

        return MainWindow(_FakeApp(**kw))

    def test_render_status_cria_uma_linha_por_item(self):
        win = self._win()
        win._render_status([
            ("ok", "Detecção Teams", "ativa"),
            ("warn", "Áudio", "indisponível"),
            ("off", "Resumo", "desativado"),
        ])
        self.assertEqual(win._rows_lay.count(), 3)
        win._render_status([("ok", "Só um", "detalhe")])  # re-render limpa os anteriores
        self.assertEqual(win._rows_lay.count(), 1)

    def test_tick_idle(self):
        win = self._win(recording=False, call_active=False)
        win.setVisible(True)
        win._tick()
        self.assertIn("Nenhuma", win._call_state.text())
        self.assertEqual(win._call_timer.text(), "—")

    def test_tick_gravando(self):
        win = self._win(recording=True)
        win.setVisible(True)
        win._tick()
        self.assertIn("Gravando", win._call_state.text())
        self.assertEqual(win._call_timer.text(), "01:12")
        # texto sem glifo (o ■ virou ícone Fluent "stop" via setIcon, #66)
        self.assertEqual(win._rec_btn.text(), "Parar e processar")
        self.assertFalse(win._rec_btn.icon().isNull())

    def test_tick_em_call_sem_gravar(self):
        win = self._win(recording=False, call_active=True)
        win.setVisible(True)
        win._tick()
        self.assertIn("Em call", win._call_state.text())
        self.assertEqual(win._call_timer.text(), "00:30")

    def test_show_update_mostra_barra(self):
        win = self._win()
        self.assertTrue(win._update_bar.isHidden())
        win.show_update("0.7.0")
        self.assertFalse(win._update_bar.isHidden())
        self.assertIn("0.7.0", win._update_lbl.text())

    def test_x_esconde_nao_fecha(self):
        win = self._win()
        win.setVisible(True)
        self.assertTrue(win.isVisible())
        win.close()  # dispara closeEvent -> ignore + hide
        self.assertFalse(win.isVisible())

    # -- capa útil (#56): recentes, stats, pendências, empty-state, clique -------

    _DADOS = {
        "total": 14, "seconds": 34920, "clients": 4,
        "recent": [
            {"meeting_title": "Reunião A", "client": "Cli A", "started_at": "2026-07-04T09:15:00",
             "duration_s": 1920, "participants": [{}, {}], "export_path": "/notas/a.md"},
            {"meeting_title": "Reunião B", "client": "Cli B", "started_at": "2026-07-03T14:00:00",
             "duration_s": 3900, "participants": [{}], "export_path": "/notas/b.md"},
        ],
        "pending": [
            {"text": "Fazer X", "title": "Reunião A", "client": "Cli A", "note_path": "/notas/a.md"},
        ],
    }

    def test_render_home_popula_stats_recentes_pendencias(self):
        win = self._win()
        win._render_home(self._DADOS)
        self.assertIn("14", win._stat_meetings._val.text())
        self.assertIn("9h42", win._stat_hours._val.text())   # 34920s = 9h42
        self.assertIn("4", win._stat_clients._val.text())
        self.assertEqual(win._recent_lay.count(), 2)          # duas reuniões recentes
        self.assertEqual(win._pending_lay.count(), 1)         # uma pendência
        self.assertEqual(win._pending_badge.text(), "1")

    def test_render_home_empty_state(self):
        win = self._win()
        win._render_home({"total": 0, "seconds": 0, "clients": 0, "recent": [], "pending": []})
        self.assertEqual(win._recent_lay.count(), 1)          # card de empty-state
        self.assertEqual(win._pending_badge.text(), "0")
        self.assertEqual(win._pending_lay.count(), 1)         # rótulo "Nada pendente"

    def test_clique_em_recente_abre_a_nota(self):
        win = self._win()
        win._open_recent("/notas/a.md")
        self.assertEqual(win.app.notes_opened, "/notas/a.md")

    def test_clique_sem_export_abre_notas_sem_selecao(self):
        win = self._win()
        win._open_recent("")
        self.assertIsNone(win.app.notes_opened)

    # -- recorte de 30 dias (#79): ativas vs backlog "M antigas" ----------------
    def test_started_within_recorte_por_data(self):
        from scriba.qt.main_window import _started_within
        cutoff = "2026-06-05T12:00:00"
        self.assertTrue(_started_within({"started_at": "2026-06-30T09:00:00"}, cutoff))   # recente
        self.assertFalse(_started_within({"started_at": "2026-03-01T09:00:00"}, cutoff))  # antiga
        self.assertFalse(_started_within({"started_at": ""}, cutoff))                     # sem data = antiga
        self.assertFalse(_started_within({}, cutoff))

    def test_render_home_badge_antigas_linka_e_some_sem_backlog(self):
        win = self._win()
        data = dict(self._DADOS, pending_stale=42, pending_days=30)
        win._render_home(data)
        # 1 item ativo + 1 rótulo "42 antiga(s)" (o último widget do layout)
        self.assertEqual(win._pending_lay.count(), 2)
        last = win._pending_lay.itemAt(win._pending_lay.count() - 1).widget()
        self.assertIn("42 antiga", last.text())
        # sem backlog: só o item ativo (o rótulo não ocupa espaço → sem layout shift)
        win._render_home(dict(self._DADOS, pending_stale=0, pending_days=30))
        self.assertEqual(win._pending_lay.count(), 1)

    def test_render_home_badge_ativas_tooltip(self):
        win = self._win()
        win._render_home(dict(self._DADOS, pending_stale=0, pending_days=30))
        self.assertEqual(win._pending_badge.text(), "1")
        self.assertIn("ativa", win._pending_badge.toolTip())

    # -- capa viva (#80): agrupamento, chips, checkbox real, badge ao vivo -------
    def test_group_by_note_agrupa_preservando_ordem(self):
        from scriba.qt.main_window import _group_by_note
        groups = _group_by_note([
            {"note_path": "/a.md", "text": "1"},
            {"note_path": "/a.md", "text": "2"},
            {"note_path": "/b.md", "text": "3"},
        ])
        self.assertEqual([g[0]["note_path"] for g in groups], ["/a.md", "/b.md"])
        self.assertEqual([len(g[1]) for g in groups], [2, 1])

    def test_short_date(self):
        from scriba.qt.main_window import _short_date
        self.assertEqual(_short_date("2026-07-04T09:15:00"), "04/07")
        self.assertEqual(_short_date(""), "")

    def test_render_home_agrupa_por_reuniao(self):
        win = self._win()
        data = {"total": 3, "seconds": 0, "clients": 2, "recent": [], "pending": [
            {"text": "X1", "title": "Reunião A", "client": "ACME", "note_path": "/a.md",
             "label": "BLOQUEANTE", "key": "", "folder": "", "started_at": "2026-07-04T09:00:00"},
            {"text": "X2", "title": "Reunião A", "client": "ACME", "note_path": "/a.md",
             "label": "ABERTO", "key": "", "folder": "", "started_at": "2026-07-04T09:00:00"},
            {"text": "Y1", "title": "Reunião B", "client": "Globex", "note_path": "/b.md",
             "label": "", "key": "", "folder": "", "started_at": "2026-07-03T09:00:00"},
        ]}
        win._render_home(data)
        self.assertEqual(win._pending_lay.count(), 2)   # 2 reuniões, não 3 subtítulos
        self.assertEqual(win._pending_badge.text(), "3")

    def test_label_chip_cores_e_vazio(self):
        win = self._win()
        self.assertIsNone(win._label_chip(""))
        self.assertEqual(win._label_chip("BLOQUEANTE — Eu").text(), "BLOQUEANTE")
        self.assertEqual(win._label_chip("ABERTO").text(), "ABERTO")
        self.assertEqual(win._label_chip("Indefinido").text(), "INDEFINIDO")  # neutro, não quebra

    def test_bump_pending_badge_nao_fica_negativo(self):
        win = self._win()
        win._pending_active_n = 2
        win._pending_badge.setVisible(True)
        win._bump_pending_badge(-1)
        self.assertEqual(win._pending_badge.text(), "1")
        win._bump_pending_badge(-5)
        self.assertEqual(win._pending_badge.text(), "0")
        self.assertFalse(win._pending_badge.isVisible())

    def test_restyle_theme_nao_retem_acento_do_tema_anterior(self):
        """Regressão do bug #70 (bordas laranjas): trocar de tema a quente deve
        re-estilizar a capa; o acento do tema anterior NÃO pode sobrar na borda do card
        de Notas (era fixado inline na construção e nunca regenerado)."""
        from scriba.qt import theme

        orig = theme._active
        self.addCleanup(lambda: setattr(theme, "_active", orig))
        win = self._win()
        win._render_home(self._DADOS)   # popula o cache p/ o re-render

        theme._active = theme.by_slug("claude")
        win.restyle_theme()
        self.assertIn(theme.by_slug("claude").accent.lower(), win.styleSheet().lower())

        theme._active = theme.by_slug("vscode")   # sai do Claude
        win.restyle_theme()
        ss = win.styleSheet().lower()
        self.assertIn(theme.by_slug("vscode").accent.lower(), ss)      # acento novo entrou
        self.assertNotIn(theme.by_slug("claude").accent.lower(), ss)   # o terracota não sobrou

    def test_restyle_theme_re_renderiza_secoes_em_cache(self):
        """restyle_theme re-renderiza recentes/pendências/serviços a partir do cache
        (sem re-coletar do disco), preservando a contagem."""
        from scriba.qt import theme

        orig = theme._active
        self.addCleanup(lambda: setattr(theme, "_active", orig))
        win = self._win()
        win._render_home(self._DADOS)
        win._render_status([("ok", "Detecção", "ativa"), ("warn", "Áudio", "x")])
        theme._active = theme.by_slug("light")
        win.restyle_theme()
        self.assertEqual(win._recent_lay.count(), 2)
        self.assertEqual(win._pending_lay.count(), 1)
        self.assertEqual(win._rows_lay.count(), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
