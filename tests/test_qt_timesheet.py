"""Smoke offscreen da janela de Apontamentos (#118/#124).

Constrói a TimesheetWindow sem app real (cfg=None -> coleta é no-op, não toca
banco), e exercita o fluxo com banco isolado em tempdir: render das 4 abas,
badges de contagem, aceitar sugestão, marcar apontado na fila, entrada rápida
e restyle_theme a partir do cache. IO roda inline (determinístico) via um
refresh guardado por flag - o __init__ dispara refresh antes do banco existir.

Roda sem display:  QT_QPA_PLATFORM=offscreen python -m unittest
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None

from scriba import timesheet_db as tsdb  # noqa: E402
from scriba import util  # noqa: E402


class _FakeApp:
    def __init__(self, cfg=None):
        self.cfg = cfg
        self.toasts = []

    def ui(self, fn):  # síncrono: collect->render determinístico nos testes
        fn()

    def _toast(self, *a):
        self.toasts.append(a)


def _cfg(tmp: Path) -> SimpleNamespace:
    return SimpleNamespace(
        timesheet=SimpleNamespace(enabled=True, suggest=True, round_minutes=15,
                                  min_meeting_minutes=10, default_client="",
                                  db_path="", export_dir="", backup_dir=""),
        output=SimpleNamespace(resolved_recordings_dir=lambda: tmp / "rec"),
    )


@unittest.skipUnless(_HAS_PYSIDE, "PySide6 não instalado (extra 'qt')")
class TimesheetWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scriba_tsui_"))
        self._app0, self._logs0, self._db0 = util.APP_DIR, util.LOGS_DIR, tsdb.DB_PATH
        util.APP_DIR = self.tmp / "app"
        util.LOGS_DIR = util.APP_DIR / "logs"
        # DB_PATH fica None: o apply_config da janela (db_path vazio) reseta para
        # None de qualquer forma — o isolamento aqui é via APP_DIR, como no app real
        tsdb.DB_PATH = None

    def tearDown(self):
        util.APP_DIR, util.LOGS_DIR, tsdb.DB_PATH = self._app0, self._logs0, self._db0
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _window(self, app):
        """Janela com refresh GUARDADO: o do __init__ é no-op; depois roda inline."""
        from scriba.qt.timesheet_ui import TimesheetWindow

        class _Win(TimesheetWindow):
            def refresh(self):
                if getattr(self, "_ready", False):
                    self._collect()

        win = _Win(app)
        win._inline_bg = True
        return win

    # -- smoke sem app real ----------------------------------------------------
    def test_constroi_sem_app_real_e_x_esconde(self):
        win = self._window(_FakeApp(cfg=None))
        self.assertEqual(win._tabs.count(), 4)
        win._ready = True
        win.refresh()  # cfg=None: no-op, sem tocar banco
        self.assertFalse(tsdb._db_path().exists())
        win.show()
        self.assertTrue(win.isVisible())
        win.close()
        self.assertFalse(win.isVisible())  # X esconde, não fecha

    # -- fluxo com banco isolado -------------------------------------------------
    def _seed(self):
        cid = tsdb.add_client("Coruripe")
        tsdb.add_project(cid, "403240")
        eid = tsdb.add_entry(work_date="2026-07-13", start_time="08:00",
                             end_time="13:00", client_id=cid, description="manha")
        tsdb.upsert_suggestion({
            "work_date": "2026-07-13", "start_time": "14:00", "end_time": "15:00",
            "client_text": "Cliente Novo", "description": "call sugerida",
            "meeting_started_at": "2026-07-13T14:00:00"})
        tsdb.add_entry(work_date="2026-07-14", start_time="09:00", end_time="10:00",
                       client_text="Delta", project_text="MM03",
                       description="dia seguinte")
        return cid, eid

    def _data_window(self):
        fake = _FakeApp(cfg=None)
        win = self._window(fake)
        fake.cfg = _cfg(self.tmp)
        win._ready = True
        win._month = "2026-07"
        win.refresh()
        return win

    def test_render_abas_e_badges(self):
        self._seed()
        win = self._data_window()
        # grade em ordem DECRESCENTE de dia (o hoje no topo): 14/07 antes de 13/07
        self.assertEqual(win._tree.topLevelItemCount(), 2)
        self.assertIn("14/07", win._tree.topLevelItem(0).text(0))
        self.assertEqual(win._tree.topLevelItem(1).childCount(), 2)
        self.assertIn("total 6:00", win._footer.text())
        self.assertIn("sugestões 1:00", win._footer.text())
        self.assertEqual(win._tabs.tabText(1), "Sugestões (1)")
        self.assertEqual(win._tabs.tabText(2), "A lançar (2)")
        # cadastro: cliente na lista e projeto no detalhe
        self.assertEqual(win._reg_list.count(), 1)
        self.assertEqual(win._proj_list.count(), 1)

    def test_checkbox_lancado_por_linha(self):
        """A coluna 'Lançado' é a coluna J (SIM/NÃO) da planilha - e numa
        SUGESTÃO, marcar = confirmar E lançar num gesto só."""
        from PySide6.QtCore import Qt

        self._seed()
        win = self._data_window()
        day13 = win._tree.topLevelItem(1)
        confirmed = next(day13.child(i) for i in range(day13.childCount())
                         if win._entry_items[day13.child(i)]["status"] == "confirmed")
        self.assertEqual(confirmed.checkState(5), Qt.Unchecked)
        confirmed.setCheckState(5, Qt.Checked)
        # a mutação é diferida (singleShot 0) para fora da pilha do setCheckState
        # - o refresh inline aqui destruiria o item AINDA EM USO (use-after-free
        # que segfaultava a suíte); processEvents dispara o timer com segurança
        self.qapp.processEvents()
        self.assertEqual(len(tsdb.list_entries(status="confirmed", posted=True)), 1)
        # sugestão TEM checkbox agora: marcar consolida (confirma + lança)
        day13 = win._tree.topLevelItem(1)   # itens recriados pelo refresh
        sug = next(day13.child(i) for i in range(day13.childCount())
                   if win._entry_items[day13.child(i)]["status"] == "suggested")
        sug.setCheckState(5, Qt.Checked)
        self.qapp.processEvents()
        self.assertEqual(len(tsdb.list_entries(status="suggested")), 0)
        self.assertEqual(len(tsdb.list_entries(status="confirmed", posted=True)), 2)

    def test_checkbox_do_dia_consolida(self):
        """Marcar o DIA consolida tudo (sugestão confirma+lança); desmarcar só
        tira o 'lançado' dos confirmados."""
        from PySide6.QtCore import Qt

        self._seed()
        win = self._data_window()
        day13 = win._tree.topLevelItem(1)
        self.assertIn(day13, win._day_items)
        self.assertEqual(day13.checkState(5), Qt.Unchecked)
        day13.setCheckState(5, Qt.Checked)
        self.qapp.processEvents()
        rows = tsdb.list_entries(day="2026-07-13")
        self.assertTrue(all(r["status"] == "confirmed" and r["posted"] for r in rows))
        # desmarca o dia: continuam confirmados, só saem do 'lançado'
        day13 = win._tree.topLevelItem(1)
        self.assertEqual(day13.checkState(5), Qt.Checked)
        day13.setCheckState(5, Qt.Unchecked)
        self.qapp.processEvents()
        rows = tsdb.list_entries(day="2026-07-13")
        self.assertTrue(all(r["status"] == "confirmed" and not r["posted"] for r in rows))

    def test_aceitar_sugestao(self):
        self._seed()
        win = self._data_window()
        win._sug_tree.setCurrentItem(win._sug_tree.topLevelItem(0))
        win._sug_accept()
        self.assertEqual(win._tabs.tabText(1), "Sugestões")
        self.assertEqual(len(tsdb.list_entries(status="suggested")), 0)
        self.assertEqual(len(tsdb.list_entries(status="confirmed")), 3)

    def test_fila_marcar_apontado(self):
        from PySide6.QtCore import Qt

        self._seed()
        win = self._data_window()
        node = win._queue_tree.topLevelItem(0)   # dias desc: 14/07 primeiro
        self.assertIn("14/07", node.text(0))
        node.child(0).setCheckState(0, Qt.Checked)
        win._queue_mark()
        self.assertEqual(win._tabs.tabText(2), "A lançar (1)")  # sobrou o dia 13
        rows = tsdb.list_entries(status="confirmed", posted=True)
        self.assertEqual(len(rows), 1)

    def test_novo_apontamento_via_dialogo(self):
        """Lançamento manual SEMPRE pelo pop-up completo (a barra rápida saiu)."""
        from scriba.qt.timesheet_ui import _EntryDialog

        self._seed()
        win = self._data_window()
        # diálogo em modo NOVO: título próprio, horários vazios (obrigam
        # preenchimento correto) e combo com cadastro + nomes crus
        defaults = dict(work_date="2026-07-15", start_time="", end_time="",
                        client_name=None, client_text="Coruripe", project_code=None,
                        project_text="", description="", overtime=0, location="")
        dlg = _EntryDialog(win, None, win._data, defaults=defaults)
        self.assertEqual(dlg.windowTitle(), "Novo apontamento")
        self.assertEqual(dlg._time_rows[0][0].text(), "")  # início vazio: obriga preencher
        self.assertEqual(dlg._client.currentText(), "Coruripe")
        names = [dlg._client.itemText(i) for i in range(dlg._client.count())]
        self.assertIn("Cliente Novo", names)
        self.assertIn("Delta", names)
        # inteligência: o projeto mais recente do cliente já vem PREENCHIDO,
        # e trocar o cliente troca o projeto junto (histórico, não só cadastro)
        self.assertEqual(dlg._project.currentText(), "403240")
        dlg._client.setCurrentText("Delta")
        self.assertEqual(dlg._project.currentText(), "MM03")
        self.assertIn("dia seguinte", dlg._desc_model.stringList())
        # diálogo sem aperto: descrição multi-linha (~240 chars) e largura mínima;
        # quebras de linha normalizam para espaço no save
        self.assertGreaterEqual(dlg.minimumWidth(), 560)
        dlg._desc.setPlainText("analise de programas z\ne leitura de emails")
        self.assertEqual(dlg.fields()["description"],
                         "analise de programas z e leitura de emails")
        # lançamento FRACIONADO: [+] adiciona bloco, [-] remove; cada bloco no fields
        self.assertEqual(len(dlg._time_rows), 1)
        dlg._add_time_row()
        self.assertEqual(len(dlg._time_rows), 2)
        dlg._time_rows[0][0].setText("08:00")
        dlg._time_rows[0][1].setText("13:00")
        dlg._time_rows[1][0].setText("14:00")
        dlg._time_rows[1][1].setText("17:00")
        self.assertEqual(dlg.fields()["blocks"], [("08:00", "13:00"), ("14:00", "17:00")])
        dlg._remove_time_row(dlg._time_rows[1][2])
        self.assertEqual(dlg.fields()["blocks"], [("08:00", "13:00")])
        # persistência: um apontamento POR BLOCO (manhã e tarde, almoço no meio)
        win._save_entry_fields({
            "work_date": "2026-07-15",
            "blocks": [("08:00", "13:00"), ("14:00", "17:00")],
            "_client": "Coruripe", "_project": "403240",
            "description": "testes unitarios", "overtime": False, "location": ""})
        rows = tsdb.list_entries(day="2026-07-15")
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["minutes"] for r in rows], [300, 180])
        self.assertTrue(all(r["client_name"] == "Coruripe" and
                            r["project_code"] == "403240" for r in rows))

    def test_editar_fracionando_um_bloco(self):
        """Editar com blocos extras: o 1º atualiza o registro, os demais viram
        apontamentos novos (ex.: tirar o almoço de uma sugestão 08:00-17:00)."""
        self._seed()
        win = self._data_window()
        alvo = tsdb.list_entries(day="2026-07-13", status="confirmed")[0]
        win._save_entry_fields({
            "work_date": "2026-07-13",
            "blocks": [("08:00", "12:00"), ("13:00", "17:00")],
            "_client": "Coruripe", "_project": "403240",
            "description": "manha", "overtime": False, "location": ""},
            entry_id=alvo["id"], accept_after=False)
        rows = tsdb.list_entries(day="2026-07-13", status="confirmed")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], alvo["id"])          # 1º bloco atualizou
        self.assertEqual(rows[0]["start_time"], "08:00")
        self.assertEqual(rows[0]["end_time"], "12:00")
        self.assertEqual(rows[1]["start_time"], "13:00")     # extra virou novo
        self.assertEqual(rows[1]["origin"], "manual")

    def test_editar_e_aceitar_preenche_projeto(self):
        """'Editar e aceitar' de sugestão sem projeto: o projeto mais recente
        do cliente vem preenchido também na EDIÇÃO (mesma inteligência do modo
        novo); projeto salvo ou digitado pelo usuário nunca é sobrescrito."""
        from scriba.qt.timesheet_ui import _EntryDialog

        self._seed()
        win = self._data_window()
        sug = win._sug_items[win._sug_tree.topLevelItem(0)]
        dlg = _EntryDialog(win, sug, win._data)
        self.assertEqual(dlg.windowTitle(), "Editar apontamento")
        # cliente da sugestão sem histórico de projeto: campo fica vazio...
        self.assertEqual(dlg._project.currentText(), "")
        # ...e corrigir o cliente para um conhecido puxa o projeto do histórico
        dlg._client.setCurrentText("Coruripe")
        self.assertEqual(dlg._project.currentText(), "403240")
        # apontamento com projeto salvo abre intocado; texto do usuário idem
        delta = tsdb.list_entries(day="2026-07-14")[0]
        dlg2 = _EntryDialog(win, delta, win._data)
        self.assertEqual(dlg2._project.currentText(), "MM03")
        dlg2._project.setCurrentText("ZMANUAL")
        dlg2._client.setCurrentText("Coruripe")
        self.assertEqual(dlg2._project.currentText(), "ZMANUAL")

    def test_defaults_do_novo_apontamento(self):
        """Emenda no dia: início = fim do último apontamento de HOJE; cliente =
        último usado (o apontamento manual não depende de call)."""
        from datetime import date

        self._seed()
        today = date.today().isoformat()
        tsdb.add_entry(work_date=today, start_time="08:00", end_time="10:15",
                       client_text="Gencau", description="analise programas z")
        win = self._data_window()
        win._month = today[:7]
        win.refresh()
        d = win._new_entry_defaults()
        self.assertEqual(d["work_date"], today)
        self.assertEqual(d["start_time"], "10:15")
        self.assertTrue(d["end_time"] == "" or d["end_time"] > "10:15")
        self.assertEqual(d["client_text"], "Gencau")

    def test_restyle_theme_re_renderiza_do_cache(self):
        self._seed()
        win = self._data_window()
        footer = win._footer.text()
        win.restyle_theme()
        self.assertEqual(win._footer.text(), footer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
