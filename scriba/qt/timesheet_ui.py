"""Janela de Apontamentos (épico #118, #124).

Quatro abas num QTabWidget, no molde das demais janelas da casa (QWidget +
remember_geometry + closeEvent->hide + restyle_theme + IO em thread com marshal
via app.ui):

1. Apontamentos - grade do mês agrupada por dia (QTreeWidget multi-coluna) com
   entrada rápida no topo e totais no rodapé; duplo clique edita.
2. Sugestões - fila de revisão do que o motor (#120) criou: aceitar, editar e
   aceitar, agrupar e aceitar (N sugestões fragmentadas viram UM apontamento),
   descartar, cadastrar cliente não resolvido, abrir a reunião.
3. A lançar - confirmados ainda não lançados no sistema de horas do usuário
   (Multi Dados etc.), com checkbox por linha e o export Excel do mês (#122).
4. Cadastro - clientes (novo/renomear/desativar/mesclar), aliases e projetos.

A janela só é alcançável com o módulo ativado (#126): o item da bandeja e o
show_timesheet do app são condicionados a [timesheet].enabled — aqui dentro
não há gate. Toda leitura/escrita do banco roda fora da thread da GUI.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QRadioButton, QSplitter, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import timesheet_db as tsdb
from .. import util
from . import theme, widgets

log = logging.getLogger("scriba.qt.timesheet")

_WEEK = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
_LOCATIONS = ("", "Base", "In Loco", "Remoto")
_COL_EXTRA = 5    # flag de hora extra (grade e fila) - coluna própria, não
                  # mais concatenada na descrição (a coluna H da planilha)
_COL_POSTED = 6   # checkbox "Lançado" da grade (a coluna J da planilha)


def _fmt_min(minutes: int) -> str:
    return f"{minutes // 60}:{minutes % 60:02d}"


def _day_label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{_WEEK[d.weekday()]} {d.strftime('%d/%m')}"


def _time_edit(tooltip: str) -> QLineEdit:
    """Campo HH:MM: dígitos viram máscara ao sair (util.format_time_hhmm)."""
    le = widgets.make_entry("hh:mm")
    le.setFixedWidth(72)
    le.setAlignment(Qt.AlignCenter)
    widgets.add_tooltip(le, tooltip)
    le.editingFinished.connect(lambda le=le: le.setText(util.format_time_hhmm(le.text())))
    return le


def _shift_month(month: str, delta: int) -> str:
    d = datetime.strptime(month, "%Y-%m")
    y, m = d.year, d.month + delta
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def _merge_entries(entries: list[dict]) -> dict:
    """Rascunho da FUSÃO de N apontamentos do mesmo dia (agrupar): horário do
    primeiro início ao último fim, descrições concatenadas com "; " (dedupe sem
    caixa, na ordem do dia), extra se QUALQUER um for extra; cliente, projeto e
    local vêm do primeiro que tiver. O resultado alimenta o _EntryDialog - o
    usuário revisa e ajusta antes de qualquer gravação."""
    sel = sorted(entries, key=lambda e: (e["start_time"], e["end_time"]))
    merged = dict(sel[0])
    merged["end_time"] = max(e["end_time"] for e in sel)
    descs: list[str] = []
    for e in sel:
        d = (e["description"] or "").strip()
        if d and d.casefold() not in (x.casefold() for x in descs):
            descs.append(d)
    merged["description"] = "; ".join(descs)
    merged["overtime"] = int(any(e["overtime"] for e in sel))
    merged["client_name"] = next((e["client_name"] for e in sel if e["client_name"]), None)
    merged["client_text"] = next((e["client_text"] for e in sel if e["client_text"]), "")
    merged["project_code"] = next((e["project_code"] for e in sel if e["project_code"]), None)
    merged["project_text"] = next((e["project_text"] for e in sel if e["project_text"]), "")
    merged["location"] = next((e["location"] for e in sel if e["location"]), "")
    return merged


class TimesheetWindow(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._titlebar_done = False
        self._inline_bg = False      # testes: True roda o collect na própria thread
        self._data = None            # último payload (restyle re-renderiza sem re-coletar)
        self._rendering = False      # setCheckState do render não é clique do usuário
        self._month = date.today().strftime("%Y-%m")
        self._day_items: dict[QTreeWidgetItem, list[dict]] = {}
        self._entry_items: dict[QTreeWidgetItem, dict] = {}
        self._sug_items: dict[QTreeWidgetItem, dict] = {}
        self._queue_items: dict[QTreeWidgetItem, dict] = {}
        self._reg_clients: dict[int, dict] = {}

        self.setWindowTitle("ScribaDev - Apontamentos")
        self.setMinimumSize(820, 560)
        widgets.remember_geometry(self, "qt_timesheet", default=(150, 110, 1020, 680))

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        self._tabs = QTabWidget()
        self._tabs.setUsesScrollButtons(False)
        self._tabs.setElideMode(Qt.ElideRight)
        self._tabs.addTab(self._build_tab_entries(), "Apontamentos")
        self._tabs.addTab(self._build_tab_suggestions(), "Sugestões")
        self._tabs.addTab(self._build_tab_queue(), "A lançar")
        self._tabs.addTab(self._build_tab_registry(), "Cadastro")
        root.addWidget(self._tabs)

        self.refresh()

    # ------------------------------------------------------------ ciclo de vida --

    def show(self):
        super().show()
        widgets.bring_to_front(self)   # macOS: ativa o app explicitamente (ver qt/__init__)
        if not self._titlebar_done:
            self._titlebar_done = True
            widgets.enable_dark_titlebar(self)
        self.refresh()

    def closeEvent(self, event):  # X esconde, não fecha (padrão da casa)
        event.ignore()
        self.hide()

    def restyle_theme(self):
        if self._data is not None:
            self._render(self._data)

    # ------------------------------------------------------------------ coleta --

    def refresh(self):
        if self._inline_bg:
            self._collect()
        else:
            threading.Thread(target=self._collect, daemon=True, name="timesheet-ui").start()

    def _collect(self):
        if getattr(self.app, "cfg", None) is None:  # smoke tests: sem app real, no-op
            return
        try:
            tsdb.apply_config(self.app.cfg.timesheet)
            month = self._month
            clients = tsdb.list_clients()
            data = {
                "month": month,
                "client_names": tsdb.known_client_names(),
                "client_history": tsdb.client_history(),
                "entries": [e for e in tsdb.list_entries(month=month)
                            if e["status"] != "discarded"],
                "totals": tsdb.day_totals(month),
                "notes": tsdb.day_notes_month(month),
                "summary": tsdb.month_summary(month),
                "suggestions": tsdb.list_entries(status="suggested"),
                "queue": tsdb.list_entries(status="confirmed", posted=False),
                "clients": clients,
                "clients_all": tsdb.list_clients(active_only=False),
                "aliases": {c["id"]: tsdb.list_aliases(c["id"]) for c in clients},
                "projects": {c["id"]: tsdb.list_projects(c["id"]) for c in clients},
            }
        except Exception:
            log.exception("coleta do timesheet falhou")
            return
        self.app.ui(lambda: self._render(data))

    def _bg(self, fn):
        """Mutação fora da GUI + refresh ao terminar. Erros viram diálogo, não crash."""
        def work():
            try:
                fn()
            except Exception as e:
                log.exception("timesheet UI: operação falhou")
                self.app.ui(lambda: QMessageBox.warning(self, "Apontamento de horas", str(e)))
            self.app.ui(self.refresh)

        if self._inline_bg:
            work()
        else:
            threading.Thread(target=work, daemon=True, name="timesheet-op").start()

    # ------------------------------------------------------ aba 1: Apontamentos --

    def _build_tab_entries(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.setSpacing(6)

        # navegação de mês
        nav = QHBoxLayout()
        nav.setSpacing(6)
        nav.addWidget(widgets.icon_button("chevron-down", "Mês anterior",
                                          lambda: self._change_month(-1)))
        self._month_combo = QComboBox()
        widgets.no_wheel_steal(self._month_combo)
        self._month_combo.setFixedWidth(110)
        for i in range(24):
            self._month_combo.addItem(_shift_month(date.today().strftime("%Y-%m"), -i))
        self._month_combo.currentTextChanged.connect(self._on_month_combo)
        nav.addWidget(self._month_combo)
        nav.addWidget(widgets.icon_button("chevron-up", "Mês seguinte",
                                          lambda: self._change_month(+1)))
        nav.addStretch(1)
        # lançamento manual SEMPRE pelo diálogo completo (pedido do uso real: a
        # barra rápida induzia lançamento incompleto; o pop-up valida tudo)
        add_btn = widgets.ModernButton("Adicionar…", self._add_manual, kind="primary")
        add_btn.setIcon(theme.qicon("edit", color=theme.active().on_accent))
        nav.addWidget(add_btn)
        nav.addWidget(widgets.ModernButton("Marcar dia…", self._mark_day))
        lay.addLayout(nav)

        self._tree = self._make_tree(
            ["Horário", "Total", "Cliente", "Projeto", "Descrição", "Extra", "Lançado"],
            widths=(150, 48, 140, 120, 0, 52, 64), stretch=4)
        # multi-seleção: "Agrupar selecionados…" do menu de contexto funde
        # apontamentos fragmentados do dia num lançamento só
        self._tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tree.itemDoubleClicked.connect(self._edit_item)
        self._tree.itemChanged.connect(self._on_entry_check)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._entries_menu)
        lay.addWidget(self._tree, 1)

        self._footer = QLabel("")
        self._footer.setProperty("role", "muted")
        lay.addWidget(self._footer)
        return page

    def _make_tree(self, headers: list[str], widths: tuple, stretch: int) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setHeaderLabels(headers)
        tree.setRootIsDecorated(True)
        tree.setSelectionMode(QAbstractItemView.SingleSelection)
        tree.setAllColumnsShowFocus(True)
        tree.header().setStretchLastSection(False)
        for i, w in enumerate(widths):
            if w:
                tree.setColumnWidth(i, w)
        tree.header().setSectionResizeMode(stretch, tree.header().ResizeMode.Stretch)
        return tree

    def _change_month(self, delta: int) -> None:
        self._set_month(_shift_month(self._month, delta))

    def _on_month_combo(self, text: str) -> None:
        if text and text != self._month:
            self._set_month(text)

    def _set_month(self, month: str) -> None:
        self._month = month
        if self._month_combo.findText(month) < 0:
            self._month_combo.addItem(month)
        self._month_combo.setCurrentText(month)
        self.refresh()

    def _add_manual(self) -> None:
        """Novo apontamento manual pelo diálogo completo (mesmo do duplo clique)."""
        dlg = _EntryDialog(self, None, self._data or {},
                           defaults=self._new_entry_defaults())
        if dlg.exec() == QDialog.Accepted:
            self._save_entry_fields(dlg.fields())

    def _new_entry_defaults(self) -> dict:
        """Pré-preenchimento do lançamento manual: o apontamento não depende de
        call (análises, e-mails, notas SAP...), então o programa emenda o dia -
        início = fim do último apontamento de hoje, fim = agora (arredondado ao
        passo) - e retoma o último cliente usado (ou o default do config)."""
        data = self._data or {}
        cfg = getattr(self.app, "cfg", None)
        ts = cfg.timesheet if cfg is not None else None
        today = date.today().isoformat()
        todays = [e for e in data.get("entries", ()) if e["work_date"] == today]
        start = max((e["end_time"] for e in todays), default="")
        end = ""
        if start:
            from .. import timesheet_suggest

            now = timesheet_suggest.round_hhmm(
                datetime.now().strftime("%H:%M"), ts.round_minutes if ts else 15)
            if now > start:
                end = now
        client = (ts.default_client if ts else "") or next(iter(data.get(
            "client_history", {})), "")
        return {"work_date": today, "start_time": start, "end_time": end,
                "client_name": None, "client_text": client, "project_code": None,
                "project_text": "", "description": "", "overtime": 0, "location": ""}

    def _mark_day(self) -> None:
        """Dia especial (feriado, férias…): diálogo próprio com data + nota."""
        from PySide6.QtWidgets import QFormLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("Marcar dia")
        lay = QVBoxLayout(dlg)
        form = QFormLayout()
        day = QDateEdit()
        day.setCalendarPopup(True)
        day.setDisplayFormat("dd/MM/yyyy")
        day.setDate(date.today())
        widgets.no_wheel_steal(day)
        note = widgets.make_entry("feriado, férias… (vazio remove a marca)")
        form.addRow("Data:", day)
        form.addRow("Nota:", note)
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Salvar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        if dlg.exec() == QDialog.Accepted:
            self._bg(lambda: tsdb.set_day_note(day.date().toPython().isoformat(),
                                               note.text()))

    def _entries_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu

        item = self._tree.itemAt(pos)
        entry = self._entry_items.get(item)
        if entry is None:
            return
        menu = QMenu(self)
        menu.addAction(theme.qicon("edit"), "Editar…", lambda: self._edit_item(item, 0))
        sel = [self._entry_items[i] for i in self._tree.selectedItems()
               if i in self._entry_items]
        if len(sel) > 1:
            menu.addAction(f"Agrupar selecionados ({len(sel)})…",
                           lambda: self._group_entries(sel))
        if entry["posted"]:
            menu.addAction("Desmarcar lançado",
                           lambda: self._bg(lambda: tsdb.set_posted([entry["id"]], False)))
        else:
            menu.addAction(theme.qicon("checkmark"), "Marcar como lançado",
                           lambda: self._bg(lambda: tsdb.set_posted([entry["id"]], True)))
        if entry["meeting_started_at"]:
            menu.addAction(theme.qicon("dismiss"), "Descartar sugestão aceita",
                           lambda: self._discard(entry))
        else:
            menu.addAction(theme.qicon("delete"), "Excluir",
                           lambda: self._delete(entry))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _delete(self, entry: dict) -> None:
        if QMessageBox.question(
                self, "Excluir apontamento",
                f"Excluir {entry['start_time']}-{entry['end_time']} de {entry['work_date']}?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self._bg(lambda: tsdb.delete_entry(entry["id"]))

    def _discard(self, entry: dict) -> None:
        if QMessageBox.question(
                self, "Descartar",
                "O apontamento desta reunião será descartado e a sugestão não voltará.\n"
                "Descartar mesmo assim?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self._bg(lambda: tsdb.update_entry(entry["id"], status="discarded"))

    def _on_entry_check(self, item, col) -> None:
        """Checkbox 'Lançado' da grade (a coluna J da planilha): marca/desmarca o
        lançamento no sistema de horas direto na linha, sem passar pela aba de fila.

        A mutação é DIFERIDA para o próximo giro do event loop (singleShot 0):
        o itemChanged é emitido no MEIO do setCheckState, e um refresh síncrono
        (modo inline dos testes) faria tree.clear() destruir o próprio item ainda
        em uso na pilha - use-after-free real, capturado pelo faulthandler na
        caça ao segfault intermitente da suíte."""
        if col != _COL_POSTED or self._rendering:
            return
        from PySide6.QtCore import QTimer

        day_entries = self._day_items.get(item)
        if day_entries is not None:
            # checkbox do DIA: marcar consolida (confirma+lança) o dia inteiro;
            # desmarcar só tira o "lançado" dos confirmados (sugestão fica como está)
            if item.checkState(_COL_POSTED) == Qt.Checked:
                ids = [e["id"] for e in day_entries]
                QTimer.singleShot(
                    0, lambda: self._bg(lambda: tsdb.confirm_and_post(ids)))
            elif item.checkState(_COL_POSTED) == Qt.Unchecked:
                ids = [e["id"] for e in day_entries
                       if e["status"] == "confirmed" and e["posted"]]
                if ids:
                    QTimer.singleShot(
                        0, lambda: self._bg(lambda: tsdb.set_posted(ids, False)))
            return
        e = self._entry_items.get(item)
        if e is None:
            return
        posted = item.checkState(_COL_POSTED) == Qt.Checked
        if e["status"] == "suggested":
            if posted:  # marcar sugestão = confirmar E lançar num gesto
                QTimer.singleShot(
                    0, lambda: self._bg(lambda: tsdb.confirm_and_post([e["id"]])))
        elif bool(e["posted"]) != posted:
            QTimer.singleShot(
                0, lambda: self._bg(lambda: tsdb.set_posted([e["id"]], posted)))

    def _edit_item(self, item, _col) -> None:
        entry = self._entry_items.get(item)
        if entry is not None:
            # editar uma sugestão É validá-la: salvar confirma (o usuário mexeu)
            self._open_editor(entry, accept_after=(entry["status"] == "suggested"))

    def _open_editor(self, entry: dict, accept_after: bool = False) -> None:
        dlg = _EntryDialog(self, entry, self._data or {})
        if dlg.exec() == QDialog.Accepted:
            self._save_entry_fields(dlg.fields(), entry_id=entry["id"],
                                    accept_after=accept_after)

    def _save_entry_fields(self, fields: dict, entry_id: int | None = None,
                           accept_after: bool = False) -> None:
        self._bg(lambda: self._persist_fields(fields, entry_id, accept_after))

    def _persist_fields(self, fields: dict, entry_id: int | None = None,
                        accept_after: bool = False) -> None:
        """Persiste os campos do diálogo: resolve cliente/projeto e grava um
        apontamento POR BLOCO de horário (lançamento fracionado). Na edição, o
        1º bloco atualiza o registro; blocos extras viram apontamentos novos
        (fracionar um bloco existente - ex.: tirar o almoço do meio). Roda FORA
        da GUI (dentro de _bg) e consome `fields` (pop)."""
        cid, text = tsdb.resolve_client(fields.pop("_client"))
        project = fields.pop("_project")
        pid, ptext = None, project
        if cid is not None and project:
            match = [p for p in tsdb.list_projects(cid)
                     if p["code"].casefold() == project.casefold()]
            if match:
                pid, ptext = match[0]["id"], ""
        blocks = fields.pop("blocks")
        common = dict(fields, client_id=cid, client_text="" if cid else text,
                      project_id=pid, project_text=ptext)
        if entry_id is None:
            for start, end in blocks:
                tsdb.add_entry(start_time=start, end_time=end, **common)
            return
        upd = dict(common, start_time=blocks[0][0], end_time=blocks[0][1])
        if accept_after:
            upd["status"] = "confirmed"
        tsdb.update_entry(entry_id, **upd)
        for start, end in blocks[1:]:  # extras: apontamentos novos (manuais)
            tsdb.add_entry(start_time=start, end_time=end, **common)

    # ------------------------------------------------------------- agrupar ----

    def _group_entries(self, sel: list[dict]) -> None:
        """Agrupa 2+ apontamentos do MESMO dia num só (a manhã fragmentada em 4
        sugestões era UM lançamento): abre o diálogo com o rascunho fundido
        (_merge_entries) para revisar - horário, descrição concatenada, tudo
        editável - e só grava no Salvar, via _apply_group. Cancelar não toca em
        nada."""
        if len(sel) < 2:
            return
        if len({e["work_date"] for e in sel}) > 1:
            QMessageBox.warning(self, "Agrupar",
                                "Selecione apontamentos do mesmo dia para agrupar.")
            return
        sel = sorted(sel, key=lambda e: (e["start_time"], e["end_time"]))
        dlg = _EntryDialog(self, _merge_entries(sel), self._data or {})
        if dlg.exec() == QDialog.Accepted:
            self._apply_group(dlg.fields(), sel[0], sel[1:])

    def _apply_group(self, fields: dict, primary: dict, rest: list[dict]) -> None:
        """Grava o agrupamento: o apontamento MAIS CEDO vira o definitivo
        (atualizado com os campos do diálogo e confirmado - agrupar é validar);
        os demais somem da grade - os de reunião viram status='discarded' (o
        tombstone preserva o meeting_started_at e segura o reprocesso), os
        manuais são apagados. O primário mantém o vínculo com a SUA reunião
        ("Abrir reunião" continua funcionando para ela)."""
        def work():
            self._persist_fields(fields, entry_id=primary["id"], accept_after=True)
            for e in rest:
                if e["meeting_started_at"]:
                    tsdb.update_entry(e["id"], status="discarded")
                else:
                    tsdb.delete_entry(e["id"])

        self._bg(work)

    # -------------------------------------------------------- aba 2: Sugestões --

    def _build_tab_suggestions(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.setSpacing(6)

        hint = QLabel("Reuniões processadas viram sugestões - revise, ajuste, agrupe e "
                      "confirme. Cliente em destaque = não cadastrado.")
        hint.setProperty("role", "muted")
        lay.addWidget(hint)

        self._sug_tree = self._make_tree(
            ["Data", "Horário", "Total", "Cliente", "Descrição"],
            widths=(84, 96, 48, 150, 0), stretch=4)
        self._sug_tree.setRootIsDecorated(False)
        # triagem em massa: aceitar/descartar valem para TODA a seleção
        self._sug_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._sug_tree.itemDoubleClicked.connect(
            lambda item, _c: self._sug_edit_accept(item))
        lay.addWidget(self._sug_tree, 1)

        btns = QHBoxLayout()
        btns.setSpacing(6)
        self._b_accept = widgets.ModernButton("Aceitar", self._sug_accept, kind="primary")
        self._b_edit = widgets.ModernButton("Editar e aceitar…",
                                            lambda: self._sug_edit_accept(None))
        self._b_group = widgets.ModernButton("Agrupar e aceitar…", self._sug_group_accept)
        widgets.add_tooltip(self._b_group,
                            "Funde as sugestões selecionadas num apontamento só - "
                            "horário do primeiro ao último, descrições concatenadas - "
                            "e abre para revisar antes de confirmar")
        self._b_discard = widgets.ModernButton("Descartar", self._sug_discard)
        self._b_client = widgets.ModernButton("Cadastrar cliente…", self._sug_register_client)
        self._b_open = widgets.ModernButton("Abrir reunião", self._sug_open_meeting)
        for b in (self._b_accept, self._b_edit, self._b_group, self._b_discard,
                  self._b_client, self._b_open):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        return page

    def _sug_selected(self) -> dict | None:
        items = self._sug_tree.selectedItems()
        return self._sug_items.get(items[0]) if items else None

    def _sug_selection(self) -> list[dict]:
        return [self._sug_items[i] for i in self._sug_tree.selectedItems()
                if i in self._sug_items]

    def _sug_accept(self) -> None:
        ids = [e["id"] for e in self._sug_selection()]
        if ids:
            self._bg(lambda: [tsdb.update_entry(i, status="confirmed") for i in ids])

    def _sug_edit_accept(self, item) -> None:
        e = self._sug_items.get(item) if item is not None else self._sug_selected()
        if e:
            self._open_editor(e, accept_after=True)

    def _sug_group_accept(self) -> None:
        sel = self._sug_selection()
        if len(sel) < 2:
            QMessageBox.information(self, "Agrupar",
                                    "Selecione duas ou mais sugestões para agrupar "
                                    "(Ctrl/Shift + clique).")
            return
        self._group_entries(sel)

    def _sug_discard(self) -> None:
        sel = self._sug_selection()
        if not sel:
            return
        if QMessageBox.question(
                self, "Descartar",
                f"{len(sel)} sugestão(ões) serão descartadas e não voltarão no "
                "reprocesso.\nDescartar mesmo assim?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        ids = [e["id"] for e in sel]
        self._bg(lambda: [tsdb.update_entry(i, status="discarded") for i in ids])

    def _sug_register_client(self) -> None:
        e = self._sug_selected()
        if e is None or not e["client_text"]:
            return
        dlg = _RegisterClientDialog(self, e["client_text"], (self._data or {}).get("clients", []))
        if dlg.exec() != QDialog.Accepted:
            return
        raw, target = e["client_text"], dlg.target_client_id()

        def work():
            cid = tsdb.add_client(raw) if target is None else target
            if target is not None:
                tsdb.add_alias(cid, raw)
            tsdb.update_entry(e["id"], client_id=cid, client_text="")

        self._bg(work)

    def _sug_open_meeting(self) -> None:
        e = self._sug_selected()
        if e is None:
            return
        from pathlib import Path

        folder = Path(e["meeting_folder"]) if e["meeting_folder"] else None
        if folder is None or not folder.exists():
            folder = self._find_meeting_folder(e["meeting_started_at"])
        if folder is None:
            QMessageBox.information(self, "Abrir reunião",
                                    "A pasta desta reunião não existe mais "
                                    "(renomeada e não reencontrada, ou removida pela retenção).")
            return
        util.open_path(folder)

    def _find_meeting_folder(self, started_at: str | None):
        """Fallback do rename: procura o meta.json do dia com o mesmo started_at."""
        if not started_at:
            return None
        try:
            import json

            d = datetime.fromisoformat(started_at)
            day_dir = (self.app.cfg.output.resolved_recordings_dir()
                       / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}")
            for meta in day_dir.glob("*/meta.json"):
                try:
                    if json.loads(meta.read_text(encoding="utf-8")).get("started_at") == started_at:
                        return meta.parent
                except (OSError, ValueError):
                    continue
        except Exception:
            log.exception("fallback de pasta da reunião falhou")
        return None

    # -------------------------------------------------------- aba 3: A lançar ----

    def _build_tab_queue(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.setSpacing(6)

        hint = QLabel("Confirmados ainda não lançados no seu sistema de horas "
                      "(Multi Dados etc.) - marque o que você já lançou lá.")
        hint.setProperty("role", "muted")
        lay.addWidget(hint)

        self._queue_tree = self._make_tree(
            ["Horário", "Total", "Cliente", "Projeto", "Descrição", "Extra"],
            widths=(150, 48, 140, 120, 0, 52), stretch=4)
        lay.addWidget(self._queue_tree, 1)

        btns = QHBoxLayout()
        btns.setSpacing(6)
        btns.addWidget(widgets.ModernButton("Marcar selecionados como lançados",
                                            self._queue_mark, kind="primary"))
        btns.addWidget(widgets.ModernButton("Exportar Excel do mês…", self._export_month))
        btns.addStretch(1)
        lay.addLayout(btns)
        return page

    def _queue_mark(self) -> None:
        ids = [e["id"] for item, e in self._queue_items.items()
               if item.checkState(0) == Qt.Checked]
        if ids:
            self._bg(lambda: tsdb.set_posted(ids, True))

    def _export_month(self) -> None:
        month = self._month
        ts = self.app.cfg.timesheet

        def work():
            from pathlib import Path

            from .. import timesheet_xlsx

            dest = Path(ts.export_dir).expanduser() if ts.export_dir else None
            out = timesheet_xlsx.export_month(month, dest)
            self.app.ui(lambda: QMessageBox.information(
                self, "Exportado", f"Excel do mês {month} gerado em:\n{out}"))

        self._bg(work)

    # ---------------------------------------------------------- aba 4: Cadastro --

    def _build_tab_registry(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(4, 8, 4, 4)
        split = QSplitter()
        lay.addWidget(split)

        left = QWidget()
        llay = QVBoxLayout(left)
        llay.setContentsMargins(0, 0, 0, 0)
        llay.setSpacing(6)
        self._reg_list = QListWidget()
        self._reg_list.currentRowChanged.connect(lambda _i: self._render_registry_detail())
        llay.addWidget(self._reg_list, 1)
        lbtn = QHBoxLayout()
        lbtn.setSpacing(6)
        lbtn.addWidget(widgets.ModernButton("Novo…", self._client_new, kind="primary"))
        lbtn.addWidget(widgets.ModernButton("Renomear…", self._client_rename))
        lbtn.addWidget(widgets.ModernButton("Desativar", self._client_deactivate))
        lbtn.addWidget(widgets.ModernButton("Mesclar em…", self._client_merge))
        llay.addLayout(lbtn)
        split.addWidget(left)

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(8, 0, 0, 0)
        rlay.setSpacing(6)
        rlay.addWidget(QLabel("Aliases (grafias que resolvem para este cliente):"))
        self._alias_list = QListWidget()
        rlay.addWidget(self._alias_list, 1)
        arow = QHBoxLayout()
        self._alias_entry = widgets.make_entry("novo alias…")
        self._alias_entry.returnPressed.connect(self._alias_add)
        arow.addWidget(self._alias_entry, 1)
        arow.addWidget(widgets.ModernButton("Adicionar", self._alias_add))
        rlay.addLayout(arow)
        rlay.addWidget(QLabel("Projetos (códigos de OS/GAP):"))
        self._proj_list = QListWidget()
        rlay.addWidget(self._proj_list, 1)
        prow = QHBoxLayout()
        self._proj_code = widgets.make_entry("código (ex.: 123456)…")
        self._proj_label = widgets.make_entry("rótulo (opcional)…")
        self._proj_code.returnPressed.connect(self._project_add)
        self._proj_label.returnPressed.connect(self._project_add)
        prow.addWidget(self._proj_code, 1)
        prow.addWidget(self._proj_label, 1)
        prow.addWidget(widgets.ModernButton("Adicionar", self._project_add))
        rlay.addLayout(prow)
        split.addWidget(right)
        widgets.remember_splitter(split, "qt_timesheet_split")
        return page

    def _reg_selected_client(self) -> dict | None:
        item = self._reg_list.currentItem()
        if item is None:
            return None
        return self._reg_clients.get(item.data(Qt.UserRole))

    def _client_new(self) -> None:
        name, ok = QInputDialog.getText(self, "Novo cliente", "Nome canônico:")
        if ok and name.strip():
            self._bg(lambda: tsdb.add_client(name))

    def _client_rename(self) -> None:
        c = self._reg_selected_client()
        if c is None:
            return
        name, ok = QInputDialog.getText(self, "Renomear cliente", "Novo nome:",
                                        text=c["name"])
        if ok and name.strip():
            self._bg(lambda: tsdb.rename_client(c["id"], name))

    def _client_deactivate(self) -> None:
        c = self._reg_selected_client()
        if c is None:
            return
        if QMessageBox.question(
                self, "Desativar cliente",
                f"Desativar {c['name']}? Ele some dos combos e do resolve; "
                "os apontamentos históricos permanecem.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self._bg(lambda: tsdb.set_client_active(c["id"], False))

    def _client_merge(self) -> None:
        c = self._reg_selected_client()
        if c is None or self._data is None:
            return
        others = [x for x in self._data["clients"] if x["id"] != c["id"]]
        if not others:
            return
        names = [x["name"] for x in others]
        name, ok = QInputDialog.getItem(
            self, "Mesclar cliente", f"Mesclar {c['name']} em:", names, 0, False)
        if ok and name:
            dst = next(x["id"] for x in others if x["name"] == name)
            self._bg(lambda: tsdb.merge_client(c["id"], dst))

    def _alias_add(self) -> None:
        c = self._reg_selected_client()
        alias = self._alias_entry.text().strip()
        if c and alias:
            self._alias_entry.clear()
            self._bg(lambda: tsdb.add_alias(c["id"], alias))

    def _project_add(self) -> None:
        c = self._reg_selected_client()
        code = self._proj_code.text().strip()
        if c and code:
            label = self._proj_label.text().strip()
            self._proj_code.clear()
            self._proj_label.clear()
            self._bg(lambda: tsdb.add_project(c["id"], code, label))

    # ------------------------------------------------------------------ render --

    def _render(self, data: dict) -> None:
        self._data = data
        self._rendering = True
        try:
            self._render_entries(data)
            self._render_suggestions(data)
            self._render_queue(data)
            self._render_registry(data)
        finally:
            self._rendering = False
        self._tabs.setTabText(1, f"Sugestões ({len(data['suggestions'])})"
                              if data["suggestions"] else "Sugestões")
        self._tabs.setTabText(2, f"A lançar ({len(data['queue'])})"
                              if data["queue"] else "A lançar")

    def _entry_cells(self, e: dict, with_date: bool = False) -> list[str]:
        # hora extra tem coluna própria (flag "extra" destacado), não entra
        # mais concatenada na descrição
        cells = [f"{e['start_time']}-{e['end_time']}", _fmt_min(e["minutes"]),
                 e["client_name"] or e["client_text"] or "-",
                 e["project_code"] or e["project_text"] or "",
                 (e["description"] or "").strip(),
                 "extra" if e["overtime"] else ""]
        if with_date:
            cells.insert(0, _day_label(e["work_date"]))
        return cells

    def _mark_extra(self, item: QTreeWidgetItem, e: dict) -> None:
        """Destaque do flag de hora extra: azul (info) e negrito - vence
        inclusive o âmbar de linha sugerida (aplicar DEPOIS)."""
        if not e["overtime"]:
            return
        item.setForeground(_COL_EXTRA, QBrush(QColor(theme.active().info)))
        f = item.font(_COL_EXTRA)
        f.setBold(True)
        item.setFont(_COL_EXTRA, f)

    def _render_entries(self, data: dict) -> None:
        t = theme.active()
        self._tree.clear()
        self._entry_items.clear()
        by_day: dict[str, list[dict]] = {}
        for e in data["entries"]:
            by_day.setdefault(e["work_date"], []).append(e)
        bold = self.font()
        bold.setBold(True)
        self._day_items.clear()
        # dias mais recentes primeiro (pedido do uso real: o hoje fica no topo)
        for day in sorted(set(by_day) | set(data["notes"]), reverse=True):
            total = data["totals"].get(day)
            node = QTreeWidgetItem(self._tree, [
                _day_label(day), _fmt_min(total) if total else "", "", "",
                data["notes"].get(day, ""), "", ""])
            for col in (0, 1):
                node.setFont(col, bold)
            for e in by_day.get(day, ()):
                # checkbox "Lançado" em TODAS as linhas (a coluna J da planilha):
                # numa sugestão, marcar = confirmar E lançar num gesto só
                item = QTreeWidgetItem(node, self._entry_cells(e) + [""])
                item.setCheckState(_COL_POSTED, Qt.Checked if e["posted"] else Qt.Unchecked)
                if e["status"] == "suggested":
                    for col in range(item.columnCount()):
                        item.setForeground(col, QBrush(QColor(t.warn)))
                self._mark_extra(item, e)
                if e["posted"]:
                    # linha já lançada fica "apagada" (tom secundário da mesma
                    # paleta) p/ as não lançadas saltarem aos olhos; aplicar por
                    # ÚLTIMO p/ vencer o azul do extra (o negrito permanece)
                    for col in range(item.columnCount()):
                        item.setForeground(col, QBrush(QColor(t.muted)))
                self._entry_items[item] = e
            entries = by_day.get(day, [])
            if entries:
                # checkbox do DIA: consolidar tudo de uma vez ("marcar esse dia")
                posted = sum(1 for e in entries if e["posted"])
                node.setCheckState(_COL_POSTED, Qt.Checked if posted == len(entries)
                                   else Qt.PartiallyChecked if posted else Qt.Unchecked)
                self._day_items[node] = entries
            else:
                node.setFlags(node.flags() & ~Qt.ItemIsUserCheckable)
            node.setExpanded(True)  # só expande DEPOIS dos filhos existirem
        s = data["summary"]
        self._footer.setText(
            f"{data['month']}: total {_fmt_min(s['total'])}  ·  "
            f"lançado {_fmt_min(s['posted'])}  ·  a lançar {_fmt_min(s['unposted'])}  ·  "
            f"sugestões {_fmt_min(s['suggested'])}")

    def _render_suggestions(self, data: dict) -> None:
        t = theme.active()
        self._sug_tree.clear()
        self._sug_items.clear()
        for e in sorted(data["suggestions"],
                        key=lambda x: (x["work_date"], x["start_time"]), reverse=True):
            item = QTreeWidgetItem(self._sug_tree, [
                e["work_date"], f"{e['start_time']}-{e['end_time']}",
                _fmt_min(e["minutes"]),
                e["client_name"] or (f"? {e['client_text']}" if e["client_text"] else "? sem cliente"),
                e["description"] or ""])
            if e["client_name"] is None:  # não resolvido: destaque de aviso
                item.setForeground(3, QBrush(QColor(t.warn)))
            self._sug_items[item] = e

    def _render_queue(self, data: dict) -> None:
        self._queue_tree.clear()
        self._queue_items.clear()
        by_day: dict[str, list[dict]] = {}
        for e in data["queue"]:
            by_day.setdefault(e["work_date"], []).append(e)
        bold = self.font()
        bold.setBold(True)
        for day in sorted(by_day, reverse=True):
            node = QTreeWidgetItem(self._queue_tree, [_day_label(day)])
            node.setFont(0, bold)
            for e in by_day[day]:
                item = QTreeWidgetItem(node, self._entry_cells(e))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Unchecked)
                self._mark_extra(item, e)
                self._queue_items[item] = e
            node.setExpanded(True)  # só expande DEPOIS dos filhos existirem

    def _render_registry(self, data: dict) -> None:
        selected = self._reg_selected_client()
        self._reg_list.clear()
        self._reg_clients = {c["id"]: c for c in data["clients_all"]}
        for c in data["clients_all"]:
            label = c["name"] + ("" if c["active"] else "  (inativo)")
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, c["id"])
            self._reg_list.addItem(item)
            if selected and c["id"] == selected["id"]:
                self._reg_list.setCurrentItem(item)
        if self._reg_list.currentRow() < 0 and self._reg_list.count():
            self._reg_list.setCurrentRow(0)
        self._render_registry_detail()

    def _render_registry_detail(self) -> None:
        self._alias_list.clear()
        self._proj_list.clear()
        c = self._reg_selected_client()
        if c is None or self._data is None:
            return
        for a in self._data["aliases"].get(c["id"], ()):
            self._alias_list.addItem(a["alias"])
        for p in self._data["projects"].get(c["id"], ()):
            label = p["code"] + (f"  -  {p['label']}" if p["label"] else "")
            self._proj_list.addItem(label)


class _DescEdit(QPlainTextEdit):
    """Descrição multi-linha (comporta ~240 caracteres visíveis) com o
    autocompletar de frases inteiras do histórico - QPlainTextEdit não tem
    setCompleter, então a fiação (popup + inserção) é manual."""

    def __init__(self, completer, rows: int = 4, parent=None):
        super().__init__(parent)
        self.setTabChangesFocus(True)
        self.setFixedHeight(self.fontMetrics().lineSpacing() * rows + 16)
        self._completer = completer
        self._inserting = False
        completer.setWidget(self)
        completer.activated.connect(self._apply_completion)
        self.textChanged.connect(self._maybe_complete)

    def _apply_completion(self, text: str) -> None:
        self._inserting = True
        try:
            self.setPlainText(text)
            cursor = self.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.setTextCursor(cursor)
        finally:
            self._inserting = False

    def _maybe_complete(self) -> None:
        # só enquanto o usuário digita (sem foco = preenchimento programático;
        # abrir o diálogo de edição não pode piscar o popup)
        if self._inserting or not self.hasFocus():
            return
        prefix = self.toPlainText().strip()
        if len(prefix) < 2:
            self._completer.popup().hide()
            return
        self._completer.setCompletionPrefix(prefix)
        if self._completer.completionCount():
            rect = self.cursorRect()
            rect.setWidth(max(260, self.width() - 12))
            self._completer.complete(rect)
        else:
            self._completer.popup().hide()


class _EntryDialog(QDialog):
    """Diálogo de lançamento: novo apontamento (entry=None, com os defaults
    inteligentes de _new_entry_defaults), edição (duplo clique na grade) e
    'Editar e aceitar' das sugestões. Projeto e sugestões de descrição seguem o
    cliente escolhido, vindos do HISTÓRICO (client_history) - funciona também
    para cliente ainda sem cadastro."""

    def __init__(self, parent, entry: dict | None, data: dict, defaults: dict | None = None):
        super().__init__(parent)
        self._data = data
        self._new = entry is None
        self._auto_project = ""
        entry = entry or defaults or {
            "work_date": date.today().isoformat(), "start_time": "", "end_time": "",
            "client_name": None, "client_text": "", "project_code": None,
            "project_text": "", "description": "", "overtime": 0, "location": "",
        }
        self.setWindowTitle("Novo apontamento" if self._new else "Editar apontamento")
        self.setMinimumWidth(560)   # sem aperto: descrição de ~240 chars respira
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)
        from PySide6.QtWidgets import QFormLayout

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self._date = QDateEdit()
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat("dd/MM/yyyy")
        self._date.setDate(date.fromisoformat(entry["work_date"]))
        widgets.no_wheel_steal(self._date)
        # blocos de horário DINÂMICOS: o [+] do 1º bloco adiciona linhas (lançamento
        # fracionado - ex.: 08:00-13:00 e 14:00-17:00 com o almoço no meio); cada
        # linha adicionada tem o [-] para remover. Cada bloco vira um apontamento.
        self._time_rows: list[tuple] = []
        times = QVBoxLayout()
        times.setSpacing(4)
        self._times_box = times
        self._add_time_row(entry["start_time"], entry["end_time"], removable=False)
        self._client = QComboBox()
        self._client.setEditable(True)
        for name in data.get("client_names", ()):  # cadastro + textos crus já usados
            self._client.addItem(name)
        self._client.setCurrentText(entry["client_name"] or entry["client_text"] or "")
        widgets.no_wheel_steal(self._client)
        self._project = QComboBox()
        self._project.setEditable(True)
        widgets.no_wheel_steal(self._project)
        # sugestões de descrição do cliente (frases recorrentes do histórico)
        from PySide6.QtCore import QStringListModel
        from PySide6.QtWidgets import QCompleter

        self._desc_model = QStringListModel(self)
        completer = QCompleter(self._desc_model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self._desc = _DescEdit(completer)
        # projeto/descrições seguem o cliente; campo de projeto vazio já vem
        # com o mais recente do cliente - também no "Editar e aceitar" de
        # sugestão sem projeto (pedido do uso real)
        self._client.currentTextChanged.connect(self._on_client_changed)
        self._on_client_changed(self._client.currentText())
        if entry["project_code"] or entry["project_text"]:
            self._project.setCurrentText(entry["project_code"] or entry["project_text"])
        self._desc.setPlainText(entry["description"] or "")
        self._extra = widgets.AnimatedCheckBox("hora extra")
        self._extra.setChecked(bool(entry["overtime"]))
        self._location = QComboBox()
        self._location.addItems(_LOCATIONS)
        self._location.setCurrentText(entry["location"] or "")
        widgets.no_wheel_steal(self._location)
        form.addRow("Data:", self._date)
        form.addRow("Horário:", times)
        form.addRow("Cliente:", self._client)
        form.addRow("Projeto:", self._project)
        form.addRow("Descrição:", self._desc)
        form.addRow("Local:", self._location)
        form.addRow("", self._extra)
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Salvar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._validate_accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _add_time_row(self, start: str = "", end: str = "", removable: bool = True) -> None:
        """Um bloco início-fim. O 1º carrega o [+]; os demais, o [-] de remoção."""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        s = _time_edit("hora de início")
        s.setText(start)
        e = _time_edit("hora de fim")
        e.setText(end)
        lay.addWidget(s)
        lay.addWidget(QLabel("até"))
        lay.addWidget(e)
        if removable:
            lay.addWidget(widgets.icon_button(
                "subtract", "Remover este bloco de horário",
                lambda: self._remove_time_row(row)))
        else:
            lay.addWidget(widgets.icon_button(
                "add", "Adicionar outro bloco de horário (lançamento fracionado - "
                       "ex.: 08:00-13:00 e 14:00-17:00, com o almoço no meio)",
                lambda: self._add_time_row()))
        lay.addStretch(1)
        self._times_box.addWidget(row)
        self._time_rows.append((s, e, row))

    def _remove_time_row(self, row) -> None:
        self._time_rows = [t for t in self._time_rows if t[2] is not row]
        row.setParent(None)
        row.deleteLater()

    def _hist_for(self, client_name: str) -> dict:
        """Histórico do cliente (projetos/descrições), casando o nome sem caixa."""
        key = client_name.strip().casefold()
        return next((h for name, h in self._data.get("client_history", {}).items()
                     if name.casefold() == key), {})

    def _on_client_changed(self, client_name: str) -> None:
        """Projetos e sugestões de descrição seguem o cliente - via HISTÓRICO
        (qualquer projeto já apontado com ele, mais recente primeiro), não só o
        cadastro. Campo vazio (ou ainda no valor de um autofill anterior) recebe
        o projeto mais recente do cliente - tanto no novo quanto na edição
        ('Editar e aceitar' de sugestão sem projeto); texto digitado pelo
        usuário nunca é sobrescrito."""
        hist = self._hist_for(client_name)
        projects = hist.get("projects", [])
        current = self._project.currentText().strip()
        self._project.clear()
        for code in projects:
            self._project.addItem(code)
        if not current or current == self._auto_project:
            self._auto_project = projects[0] if projects else ""
            self._project.setCurrentText(self._auto_project)
        else:
            self._project.setCurrentText(current)
        self._desc_model.setStringList(hist.get("descriptions", []))

    def _validate_accept(self) -> None:
        for s, e, _row in self._time_rows:
            start = util.format_time_hhmm(s.text())
            end = util.format_time_hhmm(e.text())
            if not (util.time_hhmm_ok(start) and util.time_hhmm_ok(end)):
                QMessageBox.warning(self, self.windowTitle(),
                                    "Informe início e fim no formato HH:MM "
                                    "em todos os blocos de horário.")
                return
        self.accept()

    def fields(self) -> dict:
        return {
            "work_date": self._date.date().toPython().isoformat(),
            # cada bloco (início, fim) vira UM apontamento no salvar
            "blocks": [(util.format_time_hhmm(s.text()), util.format_time_hhmm(e.text()))
                       for s, e, _row in self._time_rows],
            "_client": self._client.currentText().strip(),
            "_project": self._project.currentText().strip(),
            # quebras de linha viram espaço: a descrição é um texto único na
            # grade, no Excel e no Multi Dados (precedente do _bigtext do settings)
            "description": " ".join(self._desc.toPlainText().split()),
            "overtime": self._extra.isChecked(),
            "location": self._location.currentText(),
        }


class _RegisterClientDialog(QDialog):
    """Cliente não resolvido numa sugestão: criar novo ou virar alias de existente."""

    def __init__(self, parent, raw: str, clients: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("Cadastrar cliente")
        self._clients = clients
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"A IA identificou o cliente como: “{raw}”"))
        self._new = QRadioButton(f"Criar o cliente “{raw}”")
        self._new.setChecked(True)
        self._alias = QRadioButton("É uma grafia (alias) do cliente existente:")
        self._combo = QComboBox()
        for c in clients:
            self._combo.addItem(c["name"], c["id"])
        self._combo.setEnabled(False)
        self._alias.toggled.connect(self._combo.setEnabled)
        if not clients:
            self._alias.setEnabled(False)
        lay.addWidget(self._new)
        lay.addWidget(self._alias)
        lay.addWidget(self._combo)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def target_client_id(self) -> int | None:
        """None = criar cliente novo; senão, o id do cliente que ganha o alias."""
        if self._new.isChecked():
            return None
        return self._combo.currentData()
