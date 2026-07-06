"""Notas Qt (#49, slice 1): helpers puros + smoke offscreen (lista, seleção, render, find)."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None

_TOGGLE = "scriba:toggle-transcript"   # = notes_ui._TOGGLE_ANCHOR (link interno da transcrição)

_NOTE_MD = (
    "---\ntitulo: Boleto não gera\ndata: 2026-07-02T15:30:00\ncliente: ACME\nduracao_minutos: 42\n---\n\n"
    "## Objetivo\nInvestigar o boleto em produção.\n\n"
    "## Participantes\n- **Participante 1**: fala do financeiro\n\n"
    "## Transcrição completa\nfala um sobre boleto\nfala dois\n"
)


_MODULE_ISO = None


def setUpModule():
    # Robustez (#84): vários testes constroem NotesWindow, cujo show() dispara uma thread
    # de reindex_if_needed. Com DB_PATH dinâmico, isolar APP_DIR/DB_PATH no módulo inteiro
    # manda TODO acesso ao índice (sync e da thread) p/ um tmp — nenhum teste toca o
    # index.db REAL do usuário (o bug que zerava a capa).
    global _MODULE_ISO
    from scriba import meetings_index as _mi
    from scriba import util as _util
    tmp = Path(tempfile.mkdtemp(prefix="scriba_qtnotes_app_"))
    _MODULE_ISO = (_util.APP_DIR, _util.LOGS_DIR, _mi.DB_PATH, tmp)
    _util.APP_DIR = tmp / "app"
    _util.LOGS_DIR = _util.APP_DIR / "logs"
    _mi.DB_PATH = None   # None = resolve de util.APP_DIR (agora o tmp isolado)


def tearDownModule():
    if _MODULE_ISO is None:
        return
    import shutil
    from scriba import meetings_index as _mi
    from scriba import util as _util
    app0, logs0, db0, tmp = _MODULE_ISO
    _util.APP_DIR, _util.LOGS_DIR, _mi.DB_PATH = app0, logs0, db0
    shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class PureHelperTests(unittest.TestCase):
    def test_note_info_le_frontmatter(self):
        from scriba.qt import notes_ui

        d = Path(tempfile.mkdtemp(prefix="scriba_ni_"))
        f = d / "2026-07-02_15-30_Boleto.md"
        f.write_text(_NOTE_MD, encoding="utf-8")
        dt, dur, title = notes_ui._note_info(f)
        self.assertEqual(title, "Boleto não gera")
        self.assertEqual(dur, 42)
        self.assertEqual(dt, datetime(2026, 7, 2, 15, 30))

    def test_summary_and_transcript_separa(self):
        from scriba.qt import notes_ui

        summary, transcript = notes_ui._summary_and_transcript(_NOTE_MD)
        self.assertIn("Objetivo", summary)
        self.assertNotIn("fala um sobre boleto", summary)   # transcrição sai do resumo
        self.assertIn("fala um sobre boleto", transcript)

    def test_group_label(self):
        from scriba.qt import notes_ui

        self.assertTrue(notes_ui._group_label(date.today()).startswith("Hoje"))
        self.assertIn("/", notes_ui._group_label(date(2020, 1, 2)))

    def test_has_labelable_voices(self):
        from scriba.qt import notes_ui

        d = Path(tempfile.mkdtemp(prefix="scriba_hv_"))
        self.assertFalse(notes_ui.has_labelable_voices(d))
        (d / "voices.json").write_text('["V1", "V2"]', encoding="utf-8")
        self.assertTrue(notes_ui.has_labelable_voices(d))


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class NotesWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _win(self):
        from scriba.qt.notes_ui import NotesWindow

        base = Path(tempfile.mkdtemp(prefix="scriba_qt_notes_"))
        (base / "2026-07-02_15-30_Boleto.md").write_text(_NOTE_MD, encoding="utf-8")

        class _Out:
            def resolved_export_dir(_self): return base
            def resolved_recordings_dir(_self): return base / "_rec"

        class _App:
            cfg = type("C", (), {"output": _Out()})()
            def ui(_self, fn): fn()

        return NotesWindow(_App())

    def test_restyle_theme_smoke(self):
        from scriba.qt import theme

        win = self._win()
        orig = theme._active
        self.addCleanup(lambda: setattr(theme, "_active", orig))
        theme._active = theme.by_slug("light")
        win.restyle_theme()   # troca a quente (#70), sem nota aberta: não estoura
        self.assertIn(theme.by_slug("light").field.lower(), win._view.styleSheet().lower())

    def test_lista_agrupa_por_dia(self):
        win = self._win()
        win._refresh_list()
        self.assertGreaterEqual(win._tree.topLevelItemCount(), 1)      # nó de dia
        day = win._tree.topLevelItem(0)
        self.assertGreaterEqual(day.childCount(), 1)                    # a nota

    def test_selecionar_renderiza_e_preenche_cabecalho(self):
        win = self._win()
        win._refresh_list()
        note_item = next(iter(win._items))
        win._tree.setCurrentItem(note_item)
        win._show_selected()
        self.assertEqual(win._title.text(), "Boleto não gera")
        self.assertEqual(win._client.text(), "ACME")
        self.assertIn("Objetivo", win._view.toPlainText())
        # isVisibleTo (não isVisible): a janela-topo não está show() no teste, mas o
        # painel foi marcado visível porque a nota tem seção Participantes
        self.assertTrue(win._presentes.isVisibleTo(win))

    def test_find_acha_ocorrencias(self):
        win = self._win()
        win._refresh_list()
        win._tree.setCurrentItem(next(iter(win._items)))
        win._show_selected()
        win._find.setText("boleto")
        win._run_find()
        self.assertGreaterEqual(len(win._hits), 1)
        self.assertIn("/", win._find_count.text())                     # "N/M"

    def test_transcricao_expande_por_link_no_documento(self):
        from PySide6.QtCore import QUrl

        win = self._win()          # _NOTE_MD tem "## Transcrição completa\nfala um..."
        win._refresh_list()
        win._tree.setCurrentItem(next(iter(win._items)))
        win._show_selected()
        # o checkbox "incluir transcrição" da find bar não existe mais
        self.assertFalse(hasattr(win, "_find_transcript"))
        # colapsado: link "Mostrar", sem o corpo da transcrição
        txt = win._view.toPlainText()
        self.assertIn("Mostrar transcrição completa", txt)
        self.assertNotIn("fala um sobre boleto", txt)
        self.assertFalse(win._transcript_shown)
        # clicar no link expande no PRÓPRIO documento
        win._on_anchor(QUrl(_TOGGLE))
        self.assertTrue(win._transcript_shown)
        txt2 = win._view.toPlainText()
        self.assertIn("fala um sobre boleto", txt2)
        self.assertIn("Ocultar", txt2)
        # clicar de novo recolhe
        win._on_anchor(QUrl(_TOGGLE))
        self.assertFalse(win._transcript_shown)
        self.assertNotIn("fala um sobre boleto", win._view.toPlainText())

    def test_toggle_transcricao_nao_salta_para_hit(self):
        from PySide6.QtCore import QUrl

        win = self._win()
        win._refresh_list()
        win._tree.setCurrentItem(next(iter(win._items)))
        win._show_selected()
        win._find.setText("boleto")
        win._run_find()                     # busca ativa (jump=True move p/ o 1º hit)
        # a partir daqui, alternar a transcrição NÃO pode saltar para um hit
        jumped = []
        win._goto_hit = lambda: jumped.append(True)
        win._on_anchor(QUrl(_TOGGLE))
        self.assertEqual(jumped, [], "alternar a transcrição não deveria saltar para um hit")
        self.assertGreaterEqual(len(win._hits), 1, "os hits deveriam ser recoloridos no novo conteúdo")

    def test_salvar_so_habilita_quando_ha_mudanca(self):
        win = self._win()
        win._refresh_list()
        win._tree.setCurrentItem(next(iter(win._items)))
        win._show_selected()
        # nada mudou desde que a nota abriu -> Salvar desabilitado
        self.assertFalse(win._save_btn.isEnabled())
        # editar o título deixa "dirty" -> habilita
        win._title.setText("Boleto não gera (revisado)")
        self.assertTrue(win._save_btn.isEnabled())
        # salvar volta a limpo -> desabilita de novo
        win._save_header()
        self.assertFalse(win._save_btn.isEnabled())
        # sem nota selecionada, o cabeçalho zera e o Salvar fica desabilitado
        win._clear_header()
        self.assertFalse(win._save_btn.isEnabled())
        self.assertEqual(win._title.text(), "")

    def test_filtros_colapsaveis_com_badge(self):
        win = self._win()
        win._refresh_list()
        # colapsado por padrão, badge sem número (a busca FTS fica sempre visível fora dele)
        self.assertFalse(win._filters._open)
        self.assertEqual(win._filters._title, "Filtros")
        # ativar filtros de texto atualiza o badge
        win._f_participant.setText("ana")
        self.assertEqual(win._filters._title, "Filtros (1)")
        win._f_client.setText("acme")
        self.assertEqual(win._filters._title, "Filtros (2)")
        # o filtro de data também conta
        win._f_since._chk.setChecked(True)
        self.assertEqual(win._filters._title, "Filtros (3)")
        # limpar zera o badge
        win._f_participant.clear()
        win._f_client.clear()
        win._f_since.clear()
        self.assertEqual(win._filters._title, "Filtros")

    def test_atalhos_registrados(self):
        from PySide6.QtGui import QShortcut

        win = self._win()
        seqs = {sc.key().toString() for sc in win.findChildren(QShortcut)}
        self.assertIn("Ctrl+F", seqs)
        self.assertIn("F5", seqs)
        self.assertIn("Del", seqs)
        self.assertIn("Esc", seqs)

    def test_menu_de_contexto_da_arvore(self):
        win = self._win()
        win._refresh_list()
        item = next(iter(win._items))
        menu = win._build_tree_menu(win._items[item][0])
        texts = [a.text() for a in menu.actions() if a.text()]
        self.assertTrue(any("Abrir pasta" in t for t in texts))
        self.assertTrue(any("Excluir" in t for t in texts))
        # esta nota de teste não tem voices.json -> sem "Rotular vozes" no menu
        self.assertFalse(any("Rotular" in t for t in texts))

    def test_lista_vazia_mostra_mensagem(self):
        from scriba.qt.notes_ui import NotesWindow

        empty = Path(tempfile.mkdtemp(prefix="scriba_qt_empty_"))

        class _Out:
            def resolved_export_dir(_self): return empty
            def resolved_recordings_dir(_self): return empty / "_rec"

        class _App:
            cfg = type("C", (), {"output": _Out()})()
            def ui(_self, fn): fn()

        win = NotesWindow(_App())
        win._refresh_list()
        self.assertEqual(win._tree.topLevelItemCount(), 0)
        self.assertIn("Nenhuma nota", win._view.toPlainText())


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class NotesSlice2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_botao_rotular_vozes_aparece_com_voices(self):
        from scriba.qt.notes_ui import NotesWindow

        base = Path(tempfile.mkdtemp(prefix="scriba_qt_rv_"))
        (base / "2026-07-02_15-30_Boleto.md").write_text(_NOTE_MD, encoding="utf-8")
        rec = base / "_rec" / "2026-07-02_15-30_Boleto"      # pasta legada = rec_dir/stem
        rec.mkdir(parents=True)
        (rec / "voices.json").write_text('{"Ana": {}, "Participante 2": {}}', encoding="utf-8")

        class _Out:
            def resolved_export_dir(_s): return base
            def resolved_recordings_dir(_s): return base / "_rec"

        class _App:
            cfg = type("C", (), {"output": _Out()})()
            def ui(_s, fn): fn()

        win = NotesWindow(_App())
        win._refresh_list()
        win._tree.setCurrentItem(next(iter(win._items)))
        win._show_selected()
        # posição estável (#58): o botão nunca some (não desloca a fileira); com
        # voices.json ele fica HABILITADO (sem, ficaria visível porém desabilitado).
        self.assertTrue(win._voice_btn.isVisibleTo(win))
        self.assertTrue(win._voice_btn.isEnabled())          # a gravação tem voices.json

    def _hub(self, groups):
        from scriba.qt.notes_ui import _ActionItemsWindow

        app = type("A", (), {"cfg": type("C", (), {
            "ui": type("U", (), {"pending_window_days": 30})()})()})()
        self._revealed = []
        w = _ActionItemsWindow(app, lambda: list(groups), self._revealed.append)
        w.refresh()
        return w

    def _pend_group(self, folder):
        return {"dt": datetime(2026, 7, 2, 15, 30), "title": "Boleto",
                "path": folder / "n.md", "folder": folder, "client": "ACME",
                "items": [
                    {"key": "k1", "label": "BLOQUEANTE", "text": "corrigir CNPJ", "raw": "corrigir CNPJ"},
                    {"key": "k2", "label": "ABERTO", "text": "enviar planilha", "raw": "enviar planilha"}]}

    def test_hub_pendencias_renderiza_conta_ativas(self):
        folder = Path(tempfile.mkdtemp(prefix="scriba_qt_pend_"))
        w = self._hub([self._pend_group(folder)])
        self.assertIn("2 item", w._count.text())             # 2 ativas (default filtro Ativas)
        self.assertGreaterEqual(w._client.count(), 2)        # "Todos os clientes" + ACME

    def test_hub_filtro_bloqueantes(self):
        folder = Path(tempfile.mkdtemp(prefix="scriba_qt_blk_"))
        w = self._hub([self._pend_group(folder)])
        w._set_filter("blocking")
        self.assertIn("1 item", w._count.text())             # só o BLOQUEANTE
        w._set_filter("archived")
        self.assertIn("0 item", w._count.text())             # nada arquivado

    def test_hub_busca_por_texto(self):
        folder = Path(tempfile.mkdtemp(prefix="scriba_qt_bus_"))
        w = self._hub([self._pend_group(folder)])
        w._search.setText("planilha")
        self.assertIn("1 item", w._count.text())             # casa só "enviar planilha"

    def test_hub_clique_no_item_navega_ate_secao(self):
        folder = Path(tempfile.mkdtemp(prefix="scriba_qt_nav_"))
        g = self._pend_group(folder)
        w = self._hub([g])
        w._on_reveal_section(g["path"])                       # simula o clique
        self.assertEqual(self._revealed, [g["path"]])

    def test_hub_focus_meeting_nao_quebra_sem_grupo(self):
        # focar uma reunião que não está na lista atual não pode levantar (best-effort)
        folder = Path(tempfile.mkdtemp(prefix="scriba_qt_foc_"))
        w = self._hub([self._pend_group(folder)])
        w.focus_meeting(str(folder / "inexistente.md"))       # header não existe → no-op silencioso
        w.focus_meeting(str((folder / "n.md")))               # header existe → rola sem erro

    def test_reveal_note_at_section_posiciona_cursor_na_secao(self):
        from scriba import meetings_index as _mi
        from scriba import util as _util
        from scriba.qt.notes_ui import NotesWindow

        base = Path(tempfile.mkdtemp(prefix="scriba_qt_sec_"))
        # DB_PATH dinâmico (#84): isolar APP_DIR já manda o índice p/ o tmp
        app0, logs0 = _util.APP_DIR, _util.LOGS_DIR
        _util.APP_DIR = base / "app"; _util.LOGS_DIR = _util.APP_DIR / "logs"
        self.addCleanup(lambda: (setattr(_util, "APP_DIR", app0),
                                 setattr(_util, "LOGS_DIR", logs0)))
        self.assertTrue(str(_mi._db_path()).startswith(str(base)))  # índice no tmp, não no real
        note = base / "2026-07-02_15-30_Boleto.md"
        note.write_text("# Boleto\n\n## Resumo\ntexto do resumo\n\n"
                        "## Pendências e Ações\n- **[ABERTO]** corrigir CNPJ\n", encoding="utf-8")

        class _Out:
            def resolved_export_dir(_s): return base
            def resolved_recordings_dir(_s): return base / "_rec"

        class _App:
            cfg = type("C", (), {"output": _Out()})()
            def ui(_s, fn): fn()

        win = NotesWindow(_App())
        win._ensure_index_async = lambda: None   # sem thread de reindex (evita corrida APP_DIR)
        win._refresh_list()
        win.reveal_note_at_section(note)
        self.assertEqual(win._selected()[0], note)                      # nota selecionada
        self.assertIn("Pendências", win._view.textCursor().selectedText())  # cursor na seção

    def test_hint_de_pendencia_de_vozes_aparece_e_some_ao_rotular(self):
        from scriba.qt.notes_ui import NotesWindow

        base = Path(tempfile.mkdtemp(prefix="scriba_qt_hint_"))
        (base / "2026-07-02_15-30_Boleto.md").write_text(_NOTE_MD, encoding="utf-8")
        rec = base / "_rec" / "2026-07-02_15-30_Boleto"
        rec.mkdir(parents=True)
        vp = rec / "voices.json"
        vp.write_text(json.dumps({"Ana": {"auto": True}, "Participante 2": {}}), encoding="utf-8")

        class _Out:
            def resolved_export_dir(_s): return base
            def resolved_recordings_dir(_s): return base / "_rec"

        class _App:
            cfg = type("C", (), {"output": _Out()})()
            def ui(_s, fn): fn()

        win = NotesWindow(_App())
        win._refresh_list()
        win._tree.setCurrentItem(next(iter(win._items)))
        win._show_selected()
        # pendente: voz "Participante 2" ainda não resolvida -> hint fixo aparece
        self.assertTrue(win._voice_btn.isEnabled())
        self.assertTrue(win._voice_hint.isVisibleTo(win), "hint de pendência deveria aparecer")
        # simula a rotulagem (relabel grava labeled=True) -> estado 'done'
        vp.write_text(json.dumps({"Ana": {"auto": True}, "Bruno": {"labeled": True}}), encoding="utf-8")
        win._show_selected()
        self.assertFalse(win._voice_hint.isVisibleTo(win), "hint deveria sumir após rotular")


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class VoiceLabelStateTests(unittest.TestCase):
    """Estado da pendência de rotular vozes (speakers_ui.voice_label_state)."""

    def _folder(self, voices):
        d = Path(tempfile.mkdtemp(prefix="scriba_vls_"))
        if voices is not None:
            (d / "voices.json").write_text(json.dumps(voices), encoding="utf-8")
        return d

    def test_none_so_sem_arquivo_ou_vazio(self):
        from scriba.qt.speakers_ui import voice_label_state

        self.assertEqual(voice_label_state(self._folder(None)), "none")
        self.assertEqual(voice_label_state(self._folder({})), "none")

    def test_pending_uma_ou_mais_vozes_anonimas(self):
        from scriba.qt.speakers_ui import voice_label_state

        # 1 voz de loopback anônima já é participante a rotular (voices.json = só os outros)
        self.assertEqual(voice_label_state(self._folder({"Participante 1": {}})), "pending")
        self.assertEqual(
            voice_label_state(self._folder({"Ana": {"auto": True}, "Participante 2": {}})),
            "pending")

    def test_done_reconhecidas_ou_rotuladas(self):
        from scriba.qt.speakers_ui import voice_label_state

        # caso REAL que motivou o fix: 1 voz de loopback auto-reconhecida (ex.: "Suzi")
        self.assertEqual(voice_label_state(self._folder({"Suzi": {"auto": True}})), "done")
        self.assertEqual(  # todas auto-reconhecidas
            voice_label_state(self._folder({"Ana": {"auto": True}, "Bruno": {"auto": True}})),
            "done")
        self.assertEqual(  # o usuário rotulou pelo menos uma
            voice_label_state(self._folder({"Bruno": {"labeled": True}, "Participante 3": {}})),
            "done")


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class CommandBarTests(unittest.TestCase):
    """Guarda de regressão do #58: o botão de destaque da command bar (a ação de IA
    "Perguntar à reunião", texto + ícone) NÃO pode renderizar cortado (o bug original
    passou pelo smoke por ninguém aferir largura-real vs sizeHint)."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        from scriba.qt import theme

        cls.app = QApplication.instance() or QApplication([])
        theme.apply(cls.app)   # o QSS de QToolButton importa p/ a largura das secundárias

    def _win(self):
        from scriba.qt.notes_ui import NotesWindow

        base = Path(tempfile.mkdtemp(prefix="scriba_qt_cmd_"))
        (base / "2026-07-03_09-41_Alinhamento.md").write_text(
            "---\ntitulo: Alinhamento interno ferramentas Claude Code\n"
            "data: 2026-07-03T09:41:00\ncliente: Abaco\n---\n\n## Objetivo\nX.\n\n"
            "## Transcrição completa\nfala\n", encoding="utf-8")

        class _Out:
            def resolved_export_dir(_s): return base
            def resolved_recordings_dir(_s): return base / "_rec"

        class _App:
            cfg = type("C", (), {"output": _Out()})()
            def ui(_s, fn): fn()

        return NotesWindow(_App())

    def _select_first_note(self, w):
        for item, (_p, _t, status) in w._items.items():
            if status is None:
                w._tree.setCurrentItem(item)
                return
        self.fail("nenhuma nota real na árvore de teste")

    def test_botao_ia_em_destaque_nao_corta(self):
        w = self._win()
        w.show()
        for wd, ht in ((880, 560), (1040, 680)):
            w.resize(wd, ht)
            self.app.processEvents()
            w._refresh_list()
            self.app.processEvents()
            self._select_first_note(w)
            self.app.processEvents()
            cb = w._chat_btn   # "Perguntar à reunião": o botão de IA em destaque (texto + ícone)
            with self.subTest(size=f"{wd}x{ht}"):
                self.assertGreaterEqual(
                    cb.width(), cb.sizeHint().width(),
                    f"botão de IA cortado em {wd}x{ht}: {cb.width()} < {cb.sizeHint().width()}")
        w.hide()

    def test_ia_em_destaque_e_demais_sao_icone(self):
        from PySide6.QtWidgets import QToolButton

        w = self._win()
        w.show()
        self.app.processEvents()
        # a ação de IA é o botão de DESTAQUE: texto visível + ícone (não é só-ícone)
        self.assertEqual(w._chat_btn.text(), "Perguntar à reunião")
        self.assertFalse(w._chat_btn.icon().isNull(), "o botão de IA deveria ter ícone ao lado do texto")
        # excluir mora no overflow, fora da fileira
        acts = [a.text() for a in w._overflow_btn.menu().actions()]
        self.assertTrue(any("Excluir" in a for a in acts), f"'Excluir' fora do overflow: {acts}")
        # as demais (gerar prompt, copiar, vozes) são só-ícone
        for b in (w._prompt_btn, w._tr_btn, w._voice_btn):
            self.assertIsInstance(b, QToolButton, "ação secundária deveria ser QToolButton só-ícone")
            self.assertEqual(b.text(), "", "ação secundária não deveria ter texto")
            self.assertFalse(b.icon().isNull(), "ação secundária sem ícone")
        w.hide()


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class ChatSingleInstanceTests(unittest.TestCase):
    """Só uma janela de chat por vez (#48): mesma reunião traz à frente; outra reunião
    pede confirmação e, se confirmado, fecha a atual antes de abrir a nova."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _win(self):
        from scriba.qt.notes_ui import NotesWindow

        base = Path(tempfile.mkdtemp(prefix="scriba_qt_chat1_"))
        (base / "2026-07-02_15-30_A.md").write_text(_NOTE_MD, encoding="utf-8")
        (base / "2026-07-01_10-00_B.md").write_text(
            _NOTE_MD.replace("Boleto não gera", "Reunião B"), encoding="utf-8")

        class _Out:
            def resolved_export_dir(_s): return base
            def resolved_recordings_dir(_s): return base / "_rec"

        class _App:
            cfg = type("C", (), {"output": _Out()})()
            def ui(_s, fn): fn()

        return NotesWindow(_App())

    def _select(self, win, name_part):
        for item, (p, _t, _s) in win._items.items():
            if name_part in p.name:
                win._tree.setCurrentItem(item)
                return
        self.fail(f"nota {name_part} não encontrada na árvore")

    def test_uma_conversa_por_vez(self):
        win = self._win()
        win._refresh_list()

        # abre o chat da nota A
        self._select(win, "_A")
        win._open_chat()
        first = win._chat
        self.assertIsNotNone(first)
        self.assertTrue(win._chat_alive())

        # reabrir a MESMA nota não recria a janela (só traz à frente)
        win._open_chat()
        self.assertIs(win._chat, first, "mesma reunião deveria reusar a janela")

        # outra nota + CANCELAR mantém a conversa atual (não abre a nova)
        self._select(win, "_B")
        win._confirm_close_chat = lambda _t: False
        win._open_chat()
        self.assertIs(win._chat, first, "cancelar deveria manter a conversa atual")

        # outra nota + CONFIRMAR fecha a antiga e abre a nova
        win._confirm_close_chat = lambda _t: True
        win._open_chat()
        self.assertIsNot(win._chat, first, "confirmar deveria abrir uma nova janela")
        self.assertFalse(first.isVisible(), "a conversa anterior deveria ter sido fechada")
        win._chat.close()


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class MainWindowLiveBandSmokeTests(unittest.TestCase):
    """Faixa 'em andamento' na capa: nasce com o estágio vivo e some (vira nota) ao concluir."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _win(self):
        from scriba.qt.main_window import MainWindow

        base = Path(tempfile.mkdtemp(prefix="scriba_qt_main_"))

        class _Out:
            def resolved_recordings_dir(_s): return base / "_rec"
            def resolved_export_dir(_s): return base

        class _App:
            cfg = type("C", (), {"output": _Out(),
                                 "ui": type("U", (), {"pending_window_days": 0})()})()
            def ui(_s, fn): fn()
            def is_recording(_s): return False
            def current_call_app(_s): return None
            def show_notes(_s, *a): pass

        return MainWindow(_App())

    def test_render_live_diarizing_mostra_estagio_e_pulso(self):
        win = self._win()
        win._render_live([{"folder": "C:/x/09-31", "status": "diarizing",
                           "title": "Reunião", "started_at": "2026-07-06T09:31:00"}])
        self.assertFalse(win._live_box.isHidden())     # faixa visível
        self.assertEqual(win._live_lay.count(), 1)
        self.assertEqual(len(win._live_anims), 1)      # pulso "respirando" ativo
        win._render_live([])                           # sem em-andamento: some sem estourar
        self.assertTrue(win._live_box.isHidden())
        self.assertEqual(win._live_anims, [])          # animações paradas

    def test_apply_live_conclusao_dispara_refresh_dos_recentes(self):
        win = self._win()
        calls = []
        win.refresh_home = lambda: calls.append(1)
        win._apply_live([{"folder": "C:/x/09-31", "status": "summarizing"}],
                        {"C:/x/09-31": "summarizing"})
        self.assertFalse(win._live_box.isHidden())
        self.assertEqual(calls, [])                    # só apareceu: não mexe nos recentes
        win._apply_live([], {})                        # saiu de andamento -> virou nota pronta
        self.assertTrue(win._live_box.isHidden())
        self.assertEqual(calls, [1])                   # recentes recarregados 1x

    def test_falha_nao_pulsa_e_usa_cor_de_erro(self):
        win = self._win()
        win._render_live([{"folder": "C:/x/z", "status": "failed", "title": "X"}])
        self.assertFalse(win._live_box.isHidden())
        self.assertEqual(len(win._live_anims), 0)      # status terminal não pulsa


if __name__ == "__main__":
    unittest.main(verbosity=2)
