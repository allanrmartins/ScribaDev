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
        self.hub_opened = "unset"

    def is_recording(self): return self._rec
    def current_call_app(self): return "Teams"
    def recording_duration(self): return 72.0
    def call_duration(self): return 30.0
    def show_settings(self): pass
    def show_notes(self, note_path=None): self.notes_opened = note_path
    def show_action_hub(self, note_path=None): self.hub_opened = note_path
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

    def test_botao_horas_segue_a_ativacao(self):
        """#118/#126: atalho 'Horas' no header só com o timesheet ativado; a
        visibilidade re-sincroniza a cada refresh_home (ativação a quente)."""
        from types import SimpleNamespace

        win = self._win()
        win._collect_home = lambda: None   # só o header interessa aqui
        self.assertTrue(win._timesheet_btn.isHidden())   # cfg None = dormente
        win.refresh_home()
        self.assertTrue(win._timesheet_btn.isHidden())
        win.app.cfg = SimpleNamespace(timesheet=SimpleNamespace(enabled=True))
        win.refresh_home()
        self.assertFalse(win._timesheet_btn.isHidden())
        win.app.cfg = SimpleNamespace(timesheet=SimpleNamespace(enabled=False))
        win.refresh_home()
        self.assertTrue(win._timesheet_btn.isHidden())

    def test_menu_da_reuniao_recente_tem_excluir(self):
        """PR #137: clique-direito numa reunião recente da capa oferece abrir a
        nota, abrir a pasta (quando existe) e EXCLUIR - sem precisar ir a Notas."""
        win = self._win()
        m = {"export_path": "C:/x/nota.md", "folder": str(Path.cwd()),
             "title": "Reunião de teste", "started_at": "2026-08-15T10:00:00"}
        texts = [a.text() for a in win._recent_menu_actions(m).actions() if a.text()]
        self.assertIn("Abrir nota", texts)
        self.assertIn("Abrir pasta da gravação", texts)
        self.assertTrue(any("Excluir" in t for t in texts), texts)
        # sem pasta no disco, a ação de pasta some mas excluir permanece
        m["folder"] = str(Path.cwd() / "nao-existe-xyz")
        texts = [a.text() for a in win._recent_menu_actions(m).actions() if a.text()]
        self.assertNotIn("Abrir pasta da gravação", texts)
        self.assertTrue(any("Excluir" in t for t in texts), texts)

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

    def test_fmt_started_br(self):
        from scriba.qt.main_window import _fmt_started_br
        self.assertEqual(_fmt_started_br("2026-07-07T09:30:00"), "07/07/2026 - 09:30")
        self.assertEqual(_fmt_started_br("2026-07-06 14:03"), "06/07/2026 - 14:03")
        self.assertEqual(_fmt_started_br("2026-07-07"), "07/07/2026")   # só data
        self.assertEqual(_fmt_started_br(""), "")

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

    # -- roteamento capa → hub (#82) --------------------------------------------
    def test_open_pending_hub_roteia_para_o_hub(self):
        win = self._win()
        win._open_pending_hub("/notas/a.md")
        self.assertEqual(win.app.hub_opened, "/notas/a.md")   # abriu o hub na reunião
        win._open_pending_hub()
        self.assertIsNone(win.app.hub_opened)                 # abriu o hub sem reunião

    def test_open_pending_hub_fallback_sem_hub(self):
        # app sem show_action_hub (build antigo): cai na tela de Notas, não quebra
        win = self._win()
        win.app.show_action_hub = None   # instância shadowa o método → getattr não-callable
        win._open_pending_hub("/notas/b.md")
        self.assertEqual(win.app.notes_opened, "/notas/b.md")

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


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class LiveStripErrorTests(unittest.TestCase):
    """Faixa ao vivo mostra falhas (#90): failed/no_audio recentes entram (o estado
    vermelho de _live_item era inalcançável e a reunião que falhava sumia da capa);
    falha ANTIGA (>24h) fica fora - não vira banner eterno."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _win_com_pastas(self, metas: dict):
        """MainWindow com cfg fake apontando p/ um tmpdir com uma pasta por meta."""
        import json
        import tempfile
        from types import SimpleNamespace

        from scriba.qt.main_window import MainWindow

        root = Path(tempfile.mkdtemp(prefix="scriba_live_"))
        for name, meta in metas.items():
            d = root / name
            d.mkdir()
            (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        win = MainWindow(_FakeApp())
        win.app.cfg = SimpleNamespace(
            output=SimpleNamespace(resolved_recordings_dir=lambda: root))
        return win

    def test_failed_recente_aparece_na_faixa(self):
        from datetime import datetime

        agora = datetime.now().isoformat()
        win = self._win_com_pastas({
            "falhou": {"status": "failed", "started_at": agora, "meeting_title": "Weekly"},
        })
        win._collect_live()   # _FakeApp.ui é síncrono: aplica e renderiza já
        self.assertEqual([m.get("status") for m in win._live_cache], ["failed"])
        self.assertEqual(win._live_lay.count(), 1)
        self.assertTrue(win._live_box.isVisibleTo(win))

    def test_failed_antiga_fica_fora_mas_em_andamento_antiga_entra(self):
        from datetime import datetime, timedelta

        velho = (datetime.now() - timedelta(days=3)).isoformat()
        win = self._win_com_pastas({
            "falhou_velha": {"status": "failed", "started_at": velho},
            "presa_velha": {"status": "summarizing", "started_at": velho},
        })
        win._collect_live()
        self.assertEqual([m.get("status") for m in win._live_cache], ["summarizing"])

    def test_no_audio_recente_aparece(self):
        from datetime import datetime

        win = self._win_com_pastas({
            "muda": {"status": "no_audio", "started_at": datetime.now().isoformat()},
        })
        win._collect_live()
        self.assertEqual([m.get("status") for m in win._live_cache], ["no_audio"])

    def test_badge_de_erro_mostra_data_nao_agora(self):
        from PySide6.QtWidgets import QLabel

        from scriba.qt.main_window import MainWindow

        win = MainWindow(_FakeApp())
        erro = win._live_item({"status": "failed", "started_at": "2026-07-06T10:00:00"})
        textos = [lb.text() for lb in erro.findChildren(QLabel)]
        self.assertIn("06/07", textos)
        self.assertNotIn("agora", textos)
        vivo = win._live_item({"status": "summarizing", "started_at": "2026-07-06T10:00:00"})
        self.assertIn("agora", [lb.text() for lb in vivo.findChildren(QLabel)])


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class LivePollRobustnessTests(unittest.TestCase):
    """#91: poll serializado (1 scan em voo por vez), backoff quando ocioso e
    precedência de título unificada entre linha viva e recentes."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _win(self):
        from scriba.qt.main_window import MainWindow

        return MainWindow(_FakeApp())

    def test_poll_nao_empilha_scans(self):
        from types import SimpleNamespace
        from unittest import mock

        win = self._win()
        win.app.cfg = SimpleNamespace()   # passa o guard de "app real"
        win.show()                        # offscreen: isVisible() vira True
        self.addCleanup(win.hide)
        with mock.patch("scriba.qt.main_window.threading.Thread") as th:
            win._poll_live()              # 1º tick: dispara e marca busy
            win._poll_live()              # 2º tick com scan "em voo": não empilha
            self.assertEqual(th.call_count, 1)
        self.assertTrue(win._live_busy)

    def test_collect_libera_busy_mesmo_com_erro(self):
        win = self._win()                 # cfg None: o scan estoura e é engolido
        win._live_busy = True
        win._collect_live()
        self.assertFalse(win._live_busy)

    def test_backoff_ocioso_e_rapido_com_trabalho(self):
        win = self._win()
        win._apply_live([{"folder": "x", "status": "summarizing"}])
        self.assertEqual(win._live_timer.interval(), 1200)
        win._apply_live([])
        self.assertEqual(win._live_timer.interval(), 10_000)

    def test_display_title_precedencia(self):
        from scriba.qt.main_window import _display_title

        self.assertEqual(_display_title({"title": "A", "meeting_title": "B"}), "A")
        self.assertEqual(_display_title({"meeting_title": "B"}), "B")
        self.assertEqual(_display_title({}), "(sem título)")
        self.assertEqual(_display_title({}, "Processando…"), "Processando…")

    def test_live_item_usa_titulo_da_nota_primeiro(self):
        from PySide6.QtWidgets import QLabel

        # regressão do 78ad752 na linha VIVA: reunião renomeada e reprocessada
        # mostrava o meeting_title velho do Teams e "mudava de nome" ao concluir
        win = self._win()
        row = win._live_item({"status": "summarizing", "title": "Renomeada",
                              "meeting_title": "Meeting join | Teams"})
        textos = [lb.text() for lb in row.findChildren(QLabel)]
        self.assertTrue(any("Renomeada" in t for t in textos))
        self.assertFalse(any("Teams" in t for t in textos))


class RefreshTimesheetTests(unittest.TestCase):
    """Sugestão criada no fim de uma call tem que APARECER na janela de
    Apontamentos aberta, sem fechar e reabrir - mesmo padrão do refresh da capa:
    só toca a janela se ela existe e está visível (fechada/oculta o show()
    recarrega ao abrir)."""

    def _app(self, timesheet_win):
        from scriba.main import ScribaApp

        app = ScribaApp.__new__(ScribaApp)
        app.timesheet_win = timesheet_win
        app.ui = lambda fn: fn()   # marshal síncrono nos testes
        return app

    def test_refresh_quando_visivel(self):
        from unittest import mock

        win = mock.Mock()
        win.isVisible.return_value = True
        self._app(win).refresh_timesheet()
        win.refresh.assert_called_once()

    def test_nao_refresca_oculta_nem_inexistente(self):
        from unittest import mock

        win = mock.Mock()
        win.isVisible.return_value = False
        self._app(win).refresh_timesheet()
        win.refresh.assert_not_called()
        self._app(None).refresh_timesheet()   # janela nunca aberta: não estoura


class RefreshHomeAfterProcessTests(unittest.TestCase):
    """#90 (corrida rename x refresh): o worker agenda um refresh autoritativo da capa
    DEPOIS do reindex_renamed; o helper só toca a janela se ela existe e está visível."""

    def _app(self, main_win):
        from scriba.main import ScribaApp

        app = ScribaApp.__new__(ScribaApp)
        app.main_win = main_win
        return app

    def test_refresh_quando_visivel(self):
        from unittest import mock

        win = mock.Mock()
        win.isVisible.return_value = True
        self._app(win)._refresh_home_after_process()
        win.refresh_home.assert_called_once()

    def test_nao_refresca_oculta_nem_inexistente(self):
        from unittest import mock

        win = mock.Mock()
        win.isVisible.return_value = False
        self._app(win)._refresh_home_after_process()
        win.refresh_home.assert_not_called()
        self._app(None)._refresh_home_after_process()   # não estoura


if __name__ == "__main__":
    unittest.main(verbosity=2)
