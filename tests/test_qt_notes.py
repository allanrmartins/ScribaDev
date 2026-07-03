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

_NOTE_MD = (
    "---\ntitulo: Boleto não gera\ndata: 2026-07-02T15:30:00\ncliente: ACME\nduracao_minutos: 42\n---\n\n"
    "## Objetivo\nInvestigar o boleto em produção.\n\n"
    "## Participantes\n- **Participante 1**: fala do financeiro\n\n"
    "## Transcrição completa\nfala um sobre boleto\nfala dois\n"
)


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

    def test_janela_pendencias_renderiza(self):
        from scriba.qt.notes_ui import _ActionItemsWindow

        folder = Path(tempfile.mkdtemp(prefix="scriba_qt_pend_"))
        group = (datetime(2026, 7, 2, 15, 30), "Boleto", folder / "n.md", folder,
                 [{"key": "k1", "label": "", "text": "corrigir CNPJ", "raw": "corrigir CNPJ"}])
        revealed = []
        w = _ActionItemsWindow([group], revealed.append)
        self.assertIn("aberta", w._count.text())             # 1 item aberto

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

    def test_none_sem_arquivo_ou_uma_voz(self):
        from scriba.qt.speakers_ui import voice_label_state

        self.assertEqual(voice_label_state(self._folder(None)), "none")
        self.assertEqual(voice_label_state(self._folder({"Participante 1": {}})), "none")

    def test_pending_voz_anonima_nao_resolvida(self):
        from scriba.qt.speakers_ui import voice_label_state

        self.assertEqual(
            voice_label_state(self._folder({"Ana": {"auto": True}, "Participante 2": {}})),
            "pending")

    def test_done_reconhecidas_ou_rotuladas(self):
        from scriba.qt.speakers_ui import voice_label_state

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
