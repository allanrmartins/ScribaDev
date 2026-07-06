"""Janela de Notas do ScribaDev em PySide6 (fase B / #49). Porta `scriba/notes_ui.py`.

Dashboard das reuniões: árvore por dia (esquerda) com busca FTS + filtros estruturados
(#10/#11), e leitor de markdown NATIVO (QTextBrowser) à direita com find-in-note,
título/cliente editáveis, "Gerar Prompt de Contexto", copiar transcrição, chat
(#48), painel Presentes e progresso ao vivo das reuniões em processamento.

Ganho: o leitor usa `QTextBrowser.setMarkdown` (headers/listas/tabelas nativos) — a
`mdview.py` (render tk) some; só os helpers PUROS de parsing dela seguem
(`split_sections`). Mesma API pública que o main.py chama: show()/hide().

SLICE 1 (esta): lista + leitor + gerar-prompt/copiar/chat/excluir/salvar/presentes/find.
SLICE 2 (a seguir): sub-janela "Minhas pendências" + "Rotular vozes" (diálogo do #51).
Integração real com o ScribaApp = fase C.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import mdparse, util
from . import theme, widgets

log = logging.getLogger("scriba.qt.notes_ui")

_FRONT_DUR = re.compile(r"^duracao_minutos:\s*(\d+)", re.M)
_FRONT_TITLE = re.compile(r"^titulo:\s*(.+)$", re.M)
_FRONT_DATA = re.compile(r"^data:\s*(.+)$", re.M)
_FRONT_CLIENT = re.compile(r"^cliente:[ \t]*(.+)$", re.M)
_NAME_DT = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})")
_TRANSCRIPT_TITLE = "transcrição completa"
_TOGGLE_ANCHOR = "scriba:toggle-transcript"   # link interno p/ mostrar/ocultar a transcrição


# -- helpers puros (sem UI) ---------------------------------------------------

def _strip_frontmatter(md: str) -> str:
    lines = md.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1:]).strip()
    return md.strip()


def _summary_and_transcript(md: str) -> tuple[str, str | None]:
    """(resumo SEM a transcrição, transcrição|None) — via mdparse.split_sections (puro)."""
    pre, secs = mdparse.split_sections(_strip_frontmatter(md))
    parts = [pre.strip()] if pre.strip() else []
    transcript = None
    for title, text in secs:
        if title.strip().lower() == _TRANSCRIPT_TITLE:
            transcript = text.strip() or None
        else:
            parts.append(f"## {title}\n{text}".rstrip())
    return "\n\n".join(p for p in parts if p.strip()).strip(), transcript


def _note_info(path: Path) -> tuple[datetime, int | None, str | None]:
    """(data/hora, duração, título): frontmatter > nome do arquivo > mtime."""
    dur = title = dt = None
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:500]
        if (m := _FRONT_DUR.search(head)):
            dur = int(m.group(1))
        if (m := _FRONT_TITLE.search(head)):
            title = m.group(1).strip()
        if (m := _FRONT_DATA.search(head)):
            dt = datetime.fromisoformat(m.group(1).strip())
    except (OSError, ValueError):
        pass
    if dt is None:
        if (m := _NAME_DT.search(path.name)):
            y, mo, d, h, mi = map(int, m.groups())
            dt = datetime(y, mo, d, h, mi)
        else:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
    return dt, dur, title


def _client_of(path: Path) -> str:
    try:
        m = _FRONT_CLIENT.search(path.read_text(encoding="utf-8", errors="replace")[:500])
    except OSError:
        return ""
    return m.group(1).strip() if m else ""


def _group_label(d: date) -> str:
    today = date.today()
    prefix = "Hoje — " if d == today else ("Ontem — " if d == today - timedelta(days=1) else "")
    return f"{prefix}{d.strftime('%d/%m/%Y')}"


def has_labelable_voices(folder: Path) -> bool:
    """A gravação tem vozes rotuláveis? (voices.json com >1 voz). Puro — reusável no #51."""
    import json

    try:
        voices = json.loads((folder / "voices.json").read_text(encoding="utf-8"))
        return len(voices) > 1
    except (OSError, ValueError, TypeError):
        return False


class NotesWindow(QWidget):
    _refresh_sig = Signal()   # marshaling p/ reconstruir a lista a partir de thread

    def __init__(self, app):
        super().__init__()
        self.app = app
        self._titlebar_done = False
        self._items: dict[QTreeWidgetItem, tuple[Path, str | None, str | None]] = {}
        self._pending_sig: dict[str, str] = {}
        self._current_key: Path | None = None
        self._transcript_md: str | None = None
        self._transcript_shown = False
        self._hits: list[QTextCursor] = []
        self._hit_idx = -1
        self._chat = None            # única janela de chat ativa por vez (#48)
        self._chat_key: Path | None = None   # nota da conversa aberta
        self._chat_title = ""        # título p/ o aviso "fechar a conversa atual"
        self._header_orig: tuple[str, str] | None = None   # (título, cliente) salvos, p/ dirty-tracking

        self.setWindowTitle("ScribaDev — Notas")
        self.setMinimumSize(880, 560)
        self.setWindowOpacity(0.98)
        widgets.remember_geometry(self, "qt_notes", default=(140, 110, 1040, 680))

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split)
        split.addWidget(self._build_left())
        split.addWidget(self._build_right())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        # divisão persistida entre sessões + default proporcional com clamp (#61); o
        # minimumWidth dos dois painéis (esquerdo aqui, direito no _build_right) garante
        # que a command bar nunca seja espremida.
        widgets.remember_splitter(split, "qt_notes_splitter")

        self._search_timer = QTimer(self); self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(350); self._search_timer.timeout.connect(self._refresh_list)
        self._poll = QTimer(self); self._poll.setInterval(1500); self._poll.timeout.connect(self._poll_progress)
        self._find_timer = QTimer(self); self._find_timer.setSingleShot(True)
        self._find_timer.setInterval(200); self._find_timer.timeout.connect(self._run_find)
        self._refresh_sig.connect(self._refresh_list)
        self._apply_theme()
        self._setup_shortcuts()

    # -- construção da UI ----------------------------------------------------

    def _build_left(self) -> QWidget:
        left = QWidget()
        left.setMinimumWidth(240)   # proteção estrutural (#61): esquerda não some/estoura
        lay = QVBoxLayout(left); lay.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Reuniões"); title.setStyleSheet("font-weight:bold;")
        lay.addWidget(title)

        self._search = widgets.make_entry("filtrar notas (assunto, participante…)")
        self._search.textChanged.connect(lambda: self._search_timer.start())
        lay.addWidget(self._search)

        # filtros estruturados: COLAPSADOS por padrão (só a busca fica sempre visível),
        # com badge de quantos estão ativos no cabeçalho. Libera espaço vertical p/ a árvore.
        self._filters = widgets.Collapsible("Filtros")
        self._f_participant = widgets.make_entry("participante")
        self._f_client = widgets.make_entry("cliente")
        self._f_since = widgets.DateFilter("de")
        self._f_until = widgets.DateFilter("até")
        fbody = QWidget()
        fl = QVBoxLayout(fbody); fl.setContentsMargins(0, 4, 0, 2); fl.setSpacing(6)
        for f in (self._f_participant, self._f_client):
            f.textChanged.connect(lambda: self._search_timer.start())
            f.textChanged.connect(lambda: self._update_filter_badge())
            fl.addWidget(f)
        for f in (self._f_since, self._f_until):
            f.changed.connect(lambda: self._search_timer.start())
            f.changed.connect(lambda: self._update_filter_badge())
            fl.addWidget(f)
        self._filters.set_content(fbody)
        lay.addWidget(self._filters)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self._tree.itemSelectionChanged.connect(self._show_selected)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._tree_context_menu)
        lay.addWidget(self._tree, 1)

        # rodapé só-ícone: 3 botões de texto não cabiam no painel estreito (#64 pegou o
        # corte). Ícones + tooltip, no mesmo idioma da command bar.
        actions = QHBoxLayout()
        actions.addWidget(widgets.icon_button("refresh", "Atualizar a lista (F5)", self._refresh_list))
        actions.addWidget(widgets.icon_button("folder", "Abrir a pasta das notas", self._open_notes_dir))
        actions.addWidget(widgets.icon_button("task-list", "Minhas pendências", self._open_action_items))
        actions.addStretch(1)
        lay.addLayout(actions)
        return left

    def _active_filter_count(self) -> int:
        """Quantos filtros ESTRUTURADOS estão ativos (a busca FTS, sempre visível, não
        conta) — vira o badge do cabeçalho "Filtros"."""
        n = 0
        if self._f_participant.text().strip():
            n += 1
        if self._f_client.text().strip():
            n += 1
        if self._f_since.br():
            n += 1
        if self._f_until.br():
            n += 1
        return n

    def _update_filter_badge(self) -> None:
        n = self._active_filter_count()
        self._filters.set_header(f"Filtros ({n})" if n else "Filtros")

    def _build_right(self) -> QWidget:
        right = QWidget()
        # proteção estrutural (fatia mínima do #61): garante que a command bar caiba
        # sem cortar o botão primário, mesmo no minimumSize da janela.
        right.setMinimumWidth(500)
        lay = QVBoxLayout(right); lay.setContentsMargins(12, 0, 0, 0)

        header = QHBoxLayout()
        self._title = widgets.make_entry("título da reunião")
        self._title.textChanged.connect(lambda: self._update_save_state())
        self._title.returnPressed.connect(self._save_header)
        header.addWidget(self._title, 3)
        cli = QLabel("Cliente:"); cli.setProperty("role", "muted")
        header.addWidget(cli)
        self._client = widgets.make_entry("empresa")
        self._client.setMinimumWidth(140)   # sem fixedWidth: nomes de cliente longos não truncam
        self._client.textChanged.connect(lambda: self._update_save_state())
        self._client.returnPressed.connect(self._save_header)
        header.addWidget(self._client, 1)
        self._save_btn = widgets.ModernButton("Salvar", self._save_header)
        self._save_btn.setEnabled(False)   # dirty-only: só habilita quando título/cliente mudam
        header.addWidget(self._save_btn)
        lay.addLayout(header)

        # command bar (Fluent/Win11): a ação de IA "Perguntar à reunião" é o DESTAQUE
        # (primário colorido, texto + ícone à esquerda) — é o que conversa com a IA, não
        # pode ficar escondido atrás de um tooltip. As demais são só-ícone + tooltip; a
        # destrutiva vai pro overflow "…". Cabe no minimumSize sem cortar nada.
        acts = QHBoxLayout(); acts.setSpacing(6)
        self._chat_btn = widgets.ModernButton("Perguntar à reunião", self._open_chat, kind="primary")
        self._chat_btn.setIcon(theme.qicon("chat", color=theme.active().on_accent))
        acts.addWidget(self._chat_btn)
        self._prompt_btn = widgets.icon_button("sparkle", "Gerar Prompt de Contexto", self._copy_prompt)
        self._tr_btn = widgets.icon_button("copy", "Copiar transcrição", self._copy_transcript)
        # "Rotular vozes" tem POSIÇÃO ESTÁVEL: nunca some (não desloca os vizinhos);
        # fica desabilitado quando a gravação não tem vozes (ver _update_voice_button).
        self._voice_btn = widgets.icon_button("people", "Rotular vozes…", self._open_speaker_labeler)
        for b in (self._prompt_btn, self._tr_btn, self._voice_btn):
            acts.addWidget(b)
        acts.addStretch(1)
        self._overflow_btn = QToolButton()
        self._overflow_btn.setIcon(theme.qicon("more-horizontal"))
        self._overflow_btn.setPopupMode(QToolButton.InstantPopup)
        self._overflow_btn.setCursor(Qt.PointingHandCursor)
        widgets.add_tooltip(self._overflow_btn, "Mais ações")
        menu = QMenu(self._overflow_btn)
        self._delete_action = menu.addAction(theme.qicon("delete", color=theme.active().rec), "Excluir nota…")
        self._delete_action.triggered.connect(self._ask_delete)
        self._overflow_btn.setMenu(menu)
        acts.addWidget(self._overflow_btn)
        lay.addLayout(acts)

        # faixa de PENDÊNCIA (logo abaixo da command bar): nasce quando a reunião tem
        # vozes ainda não rotuladas e some ao rotular. Rotular participantes é uma ação
        # que fica pendente ao usuário — a faixa dá importância a ela. Clicável (abre o
        # mesmo diálogo do ícone de vozes). Numa faixa própria (não na fileira) porque o
        # texto não caberia junto do botão de IA sem cortá-lo no minimumSize.
        self._voice_hint = QLabel("Participantes ainda não rotulados - clique aqui para identificar quem falou")
        self._voice_hint.setCursor(Qt.PointingHandCursor)
        self._voice_hint.mousePressEvent = lambda _e: self._open_speaker_labeler()
        self._style_voice_hint()
        self._voice_hint.setVisible(False)
        lay.addWidget(self._voice_hint)

        # Presentes (colapsável)
        self._presentes = widgets.Collapsible("Presentes")
        lay.addWidget(self._presentes)

        # find bar (visível só com nota aberta)
        self._find_bar = QWidget()
        fl = QHBoxLayout(self._find_bar); fl.setContentsMargins(0, 0, 0, 0)
        self._find = widgets.make_entry("buscar nesta nota…")
        self._find.textChanged.connect(lambda: self._find_timer.start())
        self._find.returnPressed.connect(self._next_hit)
        fl.addWidget(self._find, 1)
        self._find_prev = widgets.icon_button("chevron-up", "Ocorrência anterior", self._prev_hit)
        self._find_next = widgets.icon_button("chevron-down", "Próxima ocorrência", self._next_hit)
        fl.addWidget(self._find_prev)
        fl.addWidget(self._find_next)
        self._find_count = QLabel(""); self._find_count.setProperty("role", "muted")
        self._find_count.setFixedWidth(48)
        fl.addWidget(self._find_count)
        lay.addWidget(self._find_bar)

        # leitor de markdown. A transcrição é expandida/recolhida por um LINK no próprio
        # documento (anchorClicked), não por um checkbox na find bar — assim a rolagem
        # é preservada e a busca não salta pro 1º hit (o antigo "a tela se realinha").
        self._view = QTextBrowser()
        self._view.setOpenLinks(False)   # nós tratamos os cliques (link interno + externos)
        self._view.anchorClicked.connect(self._on_anchor)
        lay.addWidget(self._view, 1)

        # painel de progresso (reunião em processamento)
        self._progress = QWidget()
        pl = QVBoxLayout(self._progress)
        self._prog_stage = QLabel(""); self._prog_stage.setStyleSheet("font-size:14pt; font-weight:bold;")
        self._prog_bar = QProgressBar(); self._prog_bar.setRange(0, 0); self._prog_bar.setTextVisible(False)
        self._prog_hint = QLabel(""); self._prog_hint.setWordWrap(True); self._prog_hint.setProperty("role", "muted")
        pl.addWidget(self._prog_stage); pl.addWidget(self._prog_bar); pl.addWidget(self._prog_hint); pl.addStretch(1)
        lay.addWidget(self._progress, 1)
        self._progress.hide()
        return right

    def _apply_theme(self) -> None:
        t = theme.active()
        self._view.setStyleSheet(f"QTextBrowser {{ background:{t.field}; border:1px solid {t.border};"
                                 f" border-radius:{t.radius + 2}px; padding:8px; }}")

    def _style_voice_hint(self) -> None:
        """Estilo da faixa de aviso de vozes (cor de aviso). Método próprio para re-aplicar
        na troca a quente — as cores são fixadas inline (#70)."""
        th = theme.active()
        wr = tuple(int(th.warn.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        self._voice_hint.setStyleSheet(
            f"color:{th.warn}; font-weight:bold; font-size:{th.font_size_small}pt;"
            f" background:rgba({wr[0]},{wr[1]},{wr[2]},0.14);"
            f" border:1px solid {th.warn}; border-radius:{th.radius_sm}px; padding:4px 10px;")

    def restyle_theme(self) -> None:
        """Troca a quente (#70): re-estiliza o leitor e a faixa de vozes; re-renderiza a
        nota aberta (presentes + markdown pegam a cor do tema novo) e delega às
        sub-janelas vivas (chat / itens de ação)."""
        self._apply_theme()
        self._style_voice_hint()
        if self._current_key is not None:
            md = self._selected_md()
            if md is not None:
                self._build_presentes(md)
                summary, self._transcript_md = _summary_and_transcript(md)
                self._render_view(summary)
        if self._chat is not None:
            try:
                if self._chat.isVisible():
                    self._chat.restyle_theme()
            except RuntimeError:
                self._chat = None
        # o hub de pendências é top-level próprio (app.action_hub): _restyle_top_levels
        # já chama o restyle_theme dele — nada a fazer aqui (#78).

    # -- atalhos e menu de contexto (#63) ------------------------------------

    def _setup_shortcuts(self) -> None:
        """Atalhos de teclado (ferramenta de uso diário). Del é escopado à ÁRVORE e Esc à
        busca — assim não disparam enquanto você edita texto num campo do formulário."""
        from PySide6.QtGui import QKeySequence, QShortcut

        def _sc(seq, target, slot, context=Qt.WindowShortcut) -> None:
            s = QShortcut(seq, target)
            s.setContext(context)
            s.activated.connect(slot)

        _sc(QKeySequence.Find, self, self._focus_find)                       # Ctrl+F: busca na nota
        _sc(QKeySequence(Qt.Key_F5), self, self._refresh_list)               # F5: atualiza a lista
        _sc(QKeySequence(Qt.Key_Delete), self._tree, self._ask_delete,       # Del: exclui (só na árvore)
            Qt.WidgetWithChildrenShortcut)
        _sc(QKeySequence(Qt.Key_Escape), self._find, self._reset_find,       # Esc: limpa a busca
            Qt.WidgetShortcut)

    def _focus_find(self) -> None:
        if self._find_bar.isVisible():
            self._find.setFocus()
            self._find.selectAll()

    def _tree_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None or item not in self._items or self._items[item][2] is not None:
            return   # sem item, ou reunião em processamento/falha (nota ainda não existe)
        if item not in self._tree.selectedItems():
            self._tree.setCurrentItem(item)   # não quebra uma multi-seleção existente
        menu = self._build_tree_menu(self._items[item][0])
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _build_tree_menu(self, path: Path):
        """Menu de contexto de uma nota (separado do _tree_context_menu p/ ser testável
        sem o exec() modal)."""
        t = theme.active()
        menu = QMenu(self._tree)
        menu.addAction(theme.qicon("folder"), "Abrir pasta da gravação",
                       lambda: self._open_recording_folder(path))
        from .speakers_ui import voice_label_state

        try:
            has_voices = voice_label_state(self._recording_folder_for(path)) != "none"
        except Exception:
            has_voices = False
        if has_voices:
            menu.addAction(theme.qicon("people"), "Rotular vozes…", self._open_speaker_labeler)
        menu.addSeparator()
        menu.addAction(theme.qicon("delete", color=t.rec), "Excluir nota…", self._ask_delete)
        return menu

    def _open_recording_folder(self, note_path: Path) -> None:
        folder = self._recording_folder_for(note_path)
        util.open_path(folder if folder.is_dir() else self._notes_dir())

    # -- lista ---------------------------------------------------------------

    def _notes_dir(self) -> Path:
        d = self.app.cfg.output.resolved_export_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _open_notes_dir(self) -> None:
        util.open_path(self._notes_dir())

    def _pending_meetings(self) -> list[tuple[datetime, str, Path, str | None]]:
        import json

        out = []
        try:
            metas = list(self.app.cfg.output.resolved_recordings_dir().rglob("meta.json"))
        except OSError:
            return out
        for mp in metas:
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if meta.get("status") not in util.IN_PROGRESS_STATUSES + ("failed", "no_audio"):
                continue
            started = meta.get("started_at", "")
            try:
                dt = datetime.fromisoformat(started) if started else datetime.fromtimestamp(mp.stat().st_mtime)
            except (ValueError, OSError):
                dt = datetime.now()
            out.append((dt, meta["status"], mp.parent, meta.get("title")))
        return out

    def _index_search(self, q, participant, client, since, until) -> list:
        from .. import meetings_index

        try:
            if meetings_index.count() == 0:
                return self._bruteforce(q) if q else []
            since_iso, until_iso = util.date_range_filter(since, until)
            results = meetings_index.search(
                query=q or None, participant=participant or None, client=client or None,
                since=since_iso, until=until_iso, status="done", limit=1000, include_transcript=False,
            )
        except Exception:
            return self._bruteforce(q) if q else []
        out = []
        for r in results:
            exp = r.get("export_path")
            if not exp or not Path(exp).exists():
                continue
            path = Path(exp)
            started = r.get("started_at") or ""
            try:
                dt = datetime.fromisoformat(started) if started else _note_info(path)[0]
            except ValueError:
                dt = _note_info(path)[0]
            secs = r.get("duration_s")
            out.append((dt, "note", (path, int(secs) // 60 if secs else None, r.get("title"))))
        return out

    def _bruteforce(self, q: str) -> list:
        ql = q.lower()
        out = []
        try:
            files = list(self._notes_dir().glob("*.md"))
        except OSError:
            return out
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            i = text.find("## Transcrição completa")
            if i != -1:
                text = text[:i]
            if ql not in text.lower():
                continue
            dt, dur, title = _note_info(f)
            out.append((dt, "note", (f, dur, title)))
        return out

    def _refresh_list(self) -> None:
        self._search_timer.stop()
        q = self._search.text().strip()
        participant = self._f_participant.text().strip()
        client = self._f_client.text().strip()
        since = self._f_since.br()
        until = self._f_until.br()
        searching = bool(q or participant or client or since or until)
        entries: list[tuple[datetime, str, tuple]] = []
        pending: list = []
        if searching:
            entries.extend(self._index_search(q, participant, client, since, until))
        else:
            try:
                files = list(self._notes_dir().glob("*.md"))
            except OSError:
                files = []
            for f in files:
                dt, dur, title = _note_info(f)
                entries.append((dt, "note", (f, dur, title)))
            pending = self._pending_meetings()
            for dt, status, folder, title in pending:
                entries.append((dt, "pending", (folder, status, title)))
        entries.sort(key=lambda x: x[0], reverse=True)
        self._pending_sig = {str(f): st for _dt, st, f, _t in pending}

        prev_key = self._current_key
        self._tree.clear()
        self._items.clear()
        if not entries:
            msg = ("Nenhuma reunião encontrada para a busca/filtros." if searching
                   else "Nenhuma nota ainda — elas aparecem aqui depois da primeira reunião.")
            self._show_note_pane()
            self._set_note_actions(False)
            self._clear_header()
            self._find_bar.setVisible(False)
            self._presentes.setVisible(False)
            self._view.setMarkdown(msg)
            self._current_key = None
            self._poll.start()
            return

        current_day = None
        day_node = None
        select_item = first_item = None
        for dt, kind, payload in entries:
            if dt.date() != current_day:
                current_day = dt.date()
                day_node = QTreeWidgetItem(self._tree, [_group_label(current_day)])
                day_node.setExpanded(True)
            if kind == "note":
                path, dur, title = payload
                if title:
                    short = title if len(title) <= 36 else title[:35] + "…"
                    label = f"{dt:%H:%M}  {short}"
                else:
                    label = dt.strftime("%H:%M:%S") + (f"   ·  {dur} min" if dur else "")
                item = QTreeWidgetItem(day_node, [label])
                self._items[item] = (path, title, None)
                key = path
            else:
                folder, status, title = payload
                failed = status in ("failed", "no_audio")
                item = QTreeWidgetItem(day_node, [f"{dt:%H:%M}  {util.stage_label(status)}"])
                t = theme.active()
                item.setIcon(0, theme.qicon("warning" if failed else "hourglass",
                                            color=t.warn if failed else t.muted))
                self._items[item] = (folder, title, status)
                key = folder
            first_item = first_item or item
            if key == prev_key:
                select_item = item
        target = select_item or first_item
        if target:
            self._tree.setCurrentItem(target)
        self._poll.start()

    # -- progresso -----------------------------------------------------------

    def _poll_progress(self) -> None:
        if not self.isVisible():
            self._poll.stop()
            return
        cur = {str(f): st for _dt, st, f, _t in self._pending_meetings()}
        if cur != self._pending_sig:
            self._refresh_list()

    def _show_note_pane(self) -> None:
        self._progress.hide()
        self._view.show()

    def _show_progress_pane(self) -> None:
        self._view.hide()
        self._find_bar.setVisible(False)
        self._presentes.setVisible(False)
        self._progress.show()

    def _render_progress(self, folder: Path, status: str) -> None:
        self._show_progress_pane()
        self._set_note_actions(False)
        self._clear_header()
        self._current_key = folder
        self._prog_stage.setText(util.stage_label(status))
        if status in ("failed", "no_audio"):
            self._prog_bar.setRange(0, 1)
            if status == "no_audio":
                hint = "Os arquivos de áudio desta gravação estão vazios."
            else:
                import json

                err = ""
                try:
                    err = json.loads((folder / "meta.json").read_text(encoding="utf-8")).get("error", "")
                except (OSError, ValueError):
                    pass
                hint = "O processamento desta reunião falhou."
                if err:
                    hint += f"\n\nÚltimo erro:\n{err}"
                hint += f'\n\nPara tentar de novo:\nscribadev process "{folder}"'
            self._prog_hint.setText(hint)
        else:
            self._prog_bar.setRange(0, 0)
            self._prog_hint.setText("A reunião está sendo processada localmente. O conteúdo aparece "
                                    "aqui assim que ficar pronto — pode fechar esta janela.")

    # -- visualização --------------------------------------------------------

    def _selected(self) -> tuple[Path, str | None, str | None] | None:
        items = self._tree.selectedItems()
        for it in items:
            if it in self._items:
                return self._items[it]
        return None

    def _selected_md(self) -> str | None:
        sel = self._selected()
        if not sel or sel[2] is not None:
            return None
        try:
            return sel[0].read_text(encoding="utf-8")
        except OSError:
            return None

    def _show_selected(self) -> None:
        sel = self._selected()
        if sel is None:
            return
        path, title, status = sel
        if status is not None:
            self._render_progress(path, status)
            return
        self._show_note_pane()
        title_text, client_text = title or "", _client_of(path)
        self._header_orig = (title_text, client_text)   # baseline do dirty-tracking do Salvar
        self._title.setText(title_text)
        self._client.setText(client_text)
        self._update_save_state()   # começa limpo (Salvar desabilitado)
        self._set_note_actions(True)
        self._update_voice_button(path)
        self._find_bar.setVisible(True)
        try:
            md = path.read_text(encoding="utf-8")
        except OSError as e:
            self._presentes.setVisible(False)
            self._view.setMarkdown(f"# Erro\n\nNão consegui ler {path.name}: {e}")
            return
        self._current_key = path
        self._build_presentes(md)
        summary, self._transcript_md = _summary_and_transcript(md)
        self._transcript_shown = False
        self._render_view(summary)
        self._reset_find()

    def _render_view(self, summary_md: str) -> None:
        """Mostra o resumo; se há transcrição, um LINK no fim a expande/recolhe no próprio
        documento. Colapsada por padrão. O toggle preserva a rolagem (_toggle_transcript_view)."""
        md = summary_md
        if self._transcript_md and not self._transcript_shown:
            md += f"\n\n---\n\n[Mostrar transcrição completa]({_TOGGLE_ANCHOR})"
        elif self._transcript_md and self._transcript_shown:
            md += (f"\n\n---\n\n[Ocultar transcrição completa]({_TOGGLE_ANCHOR})"
                   f"\n\n## Transcrição completa\n\n{self._transcript_md}")
        self._view.setMarkdown(md)

    def _on_anchor(self, url) -> None:
        """Clique num link do leitor: o link interno alterna a transcrição SEM navegar
        (setOpenLinks(False)); links http/mailto reais abrem no app externo."""
        if url.toString() == _TOGGLE_ANCHOR:
            self._toggle_transcript_view()
            return
        if url.scheme() in ("http", "https", "mailto"):
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(url)

    def _toggle_transcript_view(self) -> None:
        """Mostra/oculta a transcrição preservando a posição de leitura: o resumo acima
        não muda, então restaurar o valor da barra mantém o ponto; e NÃO salta para hit
        de busca (só recolore). Fim do "a tela se realinha"."""
        md = self._selected_md()
        if md is None or not self._transcript_md:
            return
        bar = self._view.verticalScrollBar()
        pos = bar.value()
        self._transcript_shown = not self._transcript_shown
        summary, self._transcript_md = _summary_and_transcript(md)
        self._render_view(summary)
        bar.setValue(min(pos, bar.maximum()))
        self._run_find(jump=False)

    # -- presentes -----------------------------------------------------------

    def _build_presentes(self, md: str) -> None:
        from .. import notes

        presentes, _ = notes.parse_participants(md)
        if not presentes:
            self._presentes.setVisible(False)
            return
        self._presentes.set_header(f"Presentes ({len(presentes)}) — quem é cada voz, segundo a IA")
        body = QWidget()
        bl = QVBoxLayout(body); bl.setContentsMargins(12, 4, 0, 0); bl.setSpacing(4)
        for label, desc in presentes.items():
            name = QLabel(f"• {label}"); name.setStyleSheet("font-weight:bold;")
            bl.addWidget(name)
            d = QLabel(desc); d.setProperty("role", "muted"); d.setWordWrap(True)
            d.setStyleSheet(f"font-size:{theme.active().font_size_small}pt;")
            bl.addWidget(d)
        self._presentes.set_content(body)
        self._presentes.setVisible(True)

    # -- ações ---------------------------------------------------------------

    def _set_note_actions(self, on: bool) -> None:
        """Liga/desliga as ações que exigem uma nota real selecionada (secundárias +
        overflow/excluir). Mantém a fileira com posição ESTÁVEL: os botões não somem,
        só ficam desabilitados. 'Rotular vozes' é refinado à parte (_update_voice_button),
        pois também depende de a gravação ter vozes."""
        for w in (self._prompt_btn, self._tr_btn, self._chat_btn):
            w.setEnabled(on)
        self._delete_action.setEnabled(on)
        self._overflow_btn.setEnabled(on)
        if not on:
            self._voice_btn.setEnabled(False)
            self._voice_hint.setVisible(False)

    def _copy_prompt(self) -> None:
        md = self._selected_md()
        if md is None:
            return
        from .. import context_prompt

        _clip(context_prompt.build_context_prompt(md))
        widgets.flash_icon(self._prompt_btn, "checkmark", theme.active().ok)

    def _copy_transcript(self) -> None:
        md = self._selected_md()
        if md is None:
            return
        _summary, tr = _summary_and_transcript(md)
        if not tr:
            QToolTip.showText(self._tr_btn.mapToGlobal(self._tr_btn.rect().bottomLeft()),
                              "Sem transcrição", self._tr_btn)
            widgets.flash_icon(self._tr_btn, "dismiss", theme.active().warn)
            return
        _clip(tr)
        widgets.flash_icon(self._tr_btn, "checkmark", theme.active().ok)

    def _open_chat(self) -> None:
        sel = self._selected()
        if not sel or sel[2] is not None:   # sem nota real selecionada
            return
        note_path = sel[0]
        md = self._selected_md()
        if md is None:
            return
        # SÓ UMA conversa por vez (#48) — não confundir perguntas de reuniões diferentes.
        if self._chat_alive():
            if self._chat_key == note_path:
                self._chat.raise_(); self._chat.activateWindow()   # mesma reunião: traz à frente
                return
            if not self._confirm_close_chat(self._chat_title or "outra reunião"):
                self._chat.raise_(); self._chat.activateWindow()   # cancelou: mantém a atual
                return
            self._chat.close()   # confirmou: fecha a anterior antes de abrir a nova
        from .chat_ui import ChatWindow

        title = sel[1] or self._title.text().strip() or "reunião"
        summary, transcript = _summary_and_transcript(md)
        self._chat = ChatWindow(summary, transcript, title)
        self._chat_key = note_path
        self._chat_title = title
        self._chat.show()

    def _chat_alive(self) -> bool:
        """Há uma janela de chat aberta (viva e visível)? Fechada pelo usuário (X) conta
        como não-aberta: o objeto persiste, mas fica invisível."""
        if self._chat is None:
            return False
        try:
            return bool(self._chat.isVisible())
        except RuntimeError:   # objeto C++ já destruído
            self._chat = None
            return False

    def _confirm_close_chat(self, other_title: str) -> bool:
        """Confirma fechar a conversa atual antes de abrir outra. Método à parte para o
        teste conseguir sobrepor (o QMessageBox é modal e bloquearia)."""
        box = QMessageBox(self)
        box.setWindowTitle("Fechar a conversa atual?")
        box.setIcon(QMessageBox.Question)
        box.setText(f'Já existe uma conversa aberta sobre "{other_title[:60]}".')
        box.setInformativeText("Só é possível uma conversa por vez. Abrir esta vai fechar a atual. Continuar?")
        box.setStandardButtons(QMessageBox.Cancel | QMessageBox.Ok)
        box.setDefaultButton(QMessageBox.Cancel)
        box.button(QMessageBox.Ok).setText("Fechar e abrir a nova")
        box.button(QMessageBox.Cancel).setText("Cancelar")
        return box.exec() == QMessageBox.Ok

    def _update_save_state(self) -> None:
        """Salvar só fica ativo quando título/cliente DIVERGEM do que está salvo (dirty).
        Autosave foi rejeitado de propósito: salvar renomeia arquivo e pasta."""
        if self._header_orig is None:
            self._save_btn.setEnabled(False)
            return
        cur = (self._title.text().strip(), self._client.text().strip())
        self._save_btn.setEnabled(cur != self._header_orig)

    def _clear_header(self) -> None:
        """Zera o cabeçalho (sem nota / em processamento) e desabilita o Salvar."""
        self._header_orig = None
        self._title.clear()
        self._client.clear()
        self._save_btn.setEnabled(False)

    def _save_header(self) -> None:
        sel = self._selected()
        if not sel or sel[2] is not None or not self._save_btn.isEnabled():
            return
        path = sel[0]
        new_title = self._title.text().strip()
        new_client = self._client.text().strip()
        from .. import notes

        # título vazio não salva: a nota (e o nome do arquivo/pasta) precisa de um título.
        # Sem esta guarda, título esvaziado era ignorado em silêncio e o "✓ Salvo" mentia.
        if not new_title:
            QMessageBox.warning(self, "Título obrigatório",
                                "A nota precisa de um título. Preencha o campo antes de salvar.")
            return
        # Fonte da verdade é a pasta da gravação (meta.json + notas.md), de onde o índice
        # lê — update_note_meta sincroniza meta/notas/.md exportado (e o .md EXIBIDO, passado
        # como alvo extra: pode divergir do export_path se este ficou obsoleto) + reindexa,
        # p/ a capa e a busca por cliente refletirem. Fallback (pasta sumiu): edita só o .md.
        folder = self._recording_folder_for(path)
        if not (folder.exists() and notes.update_note_meta(
                folder, title=new_title, client=new_client, extra_targets=[path])):
            notes.set_note_title(path, new_title)
            notes.set_note_client(path, new_client)
        self._header_orig = (new_title, new_client)   # salvo agora -> volta a limpo
        self._update_save_state()
        self._refresh_list()
        # a capa lê do índice: pede o refresh p/ a edição aparecer lá sem reabrir a janela
        mw = getattr(self.app, "main_win", None)
        if mw is not None:
            mw.refresh_home()
        widgets.flash_button(self._save_btn, "✓ Salvo", "Salvar")

    def _ask_delete(self) -> None:
        targets = [(p, t) for it in self._tree.selectedItems()
                   if it in self._items
                   for (p, t, s) in [self._items[it]] if s is None]
        if not targets:
            return
        n = len(targets)
        nome = (targets[0][1] or targets[0][0].stem.replace("_reuniao", "")).strip()
        text = (f"Excluir a nota \"{nome[:60]}\"?" if n == 1 else f"Excluir {n} notas?")
        box = QMessageBox(self)
        box.setWindowTitle("Excluir nota" + ("s" if n > 1 else ""))
        box.setIcon(QMessageBox.Warning)
        box.setText(text)
        box.setInformativeText("A nota sai da lista e do índice de busca. Esta ação não pode ser desfeita.")
        also = widgets.AnimatedCheckBox("Excluir também o áudio/gravação (sem volta)")
        box.setCheckBox(also)
        box.setStandardButtons(QMessageBox.Cancel | QMessageBox.Yes)
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec() != QMessageBox.Yes:
            return
        self._do_delete([p for p, _ in targets], also.isChecked())

    def _do_delete(self, paths: list[Path], also_audio: bool) -> None:
        from .. import meetings_index

        for note_path in paths:
            try:
                note_path.unlink(missing_ok=True)
            except OSError as e:
                log.warning("exclusão: %s: %s", note_path.name, e)
            folder = self._recording_folder_for(note_path)
            if also_audio and folder.is_dir() and not util.is_locked(folder):
                import shutil

                meetings_index.remove_meeting(folder)
                try:
                    shutil.rmtree(folder)
                except OSError as e:
                    log.warning("exclusão gravação: %s: %s", folder.name, e)
            else:
                meetings_index.mark_deleted(folder)
        self._current_key = None
        self._refresh_list()

    def _recording_folder_for(self, note_path: Path) -> Path:
        import json

        rec_dir = self.app.cfg.output.resolved_recordings_dir()
        stem = note_path.stem.replace("_reuniao", "")
        legacy = rec_dir / stem
        if legacy.exists():
            return legacy
        m = _NAME_DT.search(stem)
        if not m:
            return legacy
        y, mo, d, h, mi = m.groups()
        suffix = stem[m.end():]
        exact = rec_dir / y / mo / d / f"{h}-{mi}{suffix}"
        if exact.exists():
            return exact
        day_dir = rec_dir / y / mo / d
        if day_dir.is_dir():
            for cand in sorted(day_dir.glob(f"{h}-{mi}*")):
                try:
                    exp = json.loads((cand / "meta.json").read_text(encoding="utf-8")).get("export_path", "")
                except (OSError, ValueError):
                    continue
                if exp and Path(exp).name == note_path.name:
                    return cand
        return exact

    # -- find-in-note --------------------------------------------------------

    def _run_find(self, jump: bool = True) -> None:
        query = self._find.text().strip()
        self._clear_hits()
        if not query:
            self._find_count.setText("")
            return
        doc = self._view.document()
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(theme.active().highlight))
        fmt.setForeground(QColor(theme.active().on_highlight))
        cursor = QTextCursor(doc)
        self._hits = []
        sels = []
        while True:
            cursor = doc.find(query, cursor)
            if cursor.isNull():
                break
            self._hits.append(QTextCursor(cursor))
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format = fmt
            sels.append(sel)
        self._view.setExtraSelections(sels)
        if self._hits:
            self._hit_idx = 0
            if jump:
                self._goto_hit()
            else:   # recolore os hits sem mover o cursor/rolagem (toggle da transcrição)
                self._find_count.setText(f"1/{len(self._hits)}")
        else:
            self._find_count.setText("0/0")

    def _clear_hits(self) -> None:
        self._view.setExtraSelections([])
        self._hits = []
        self._hit_idx = -1

    def _goto_hit(self) -> None:
        if not self._hits:
            return
        cur = QTextCursor(self._hits[self._hit_idx])
        self._view.setTextCursor(cur)
        self._view.ensureCursorVisible()
        self._find_count.setText(f"{self._hit_idx + 1}/{len(self._hits)}")

    def _next_hit(self) -> None:
        if self._hits:
            self._hit_idx = (self._hit_idx + 1) % len(self._hits)
            self._goto_hit()

    def _prev_hit(self) -> None:
        if self._hits:
            self._hit_idx = (self._hit_idx - 1) % len(self._hits)
            self._goto_hit()

    def _reset_find(self) -> None:
        self._find.clear()
        self._clear_hits()
        self._find_count.setText("")

    # -- rotular vozes (#1; diálogo do #51) ----------------------------------

    def _update_voice_button(self, note_path: Path) -> None:
        from .speakers_ui import voice_label_state

        try:
            state = voice_label_state(self._recording_folder_for(note_path))
        except Exception:
            state = "none"
        t = theme.active()
        self._voice_btn.setEnabled(state != "none")
        if state == "done":
            # já rotulado: ícone VERDE, sem hint; segue clicável para revisar/alterar
            self._voice_btn.setIcon(theme.qicon("people", color=t.ok))
            widgets.add_tooltip(self._voice_btn, "Participantes rotulados — clique para revisar")
        else:
            self._voice_btn.setIcon(theme.qicon("people"))
            widgets.add_tooltip(self._voice_btn,
                                "Rotular participantes" if state == "pending"
                                else "Esta gravação não tem vozes rotuláveis")
        self._voice_hint.setVisible(state == "pending")

    def _open_speaker_labeler(self) -> None:
        sel = self._selected()
        if not sel or sel[2] is not None:
            return
        from .speakers_ui import label_speakers_dialog

        folder = self._recording_folder_for(sel[0])
        self._voice_dlg = label_speakers_dialog(   # ref evita GC
            folder, on_saved=self._after_relabel, guesses=self._voice_guesses(sel[0], folder))

    def _voice_guesses(self, note_path: Path, folder: Path) -> dict[str, str]:
        import json

        from .. import notes

        try:
            presentes, _ = notes.parse_participants(note_path.read_text(encoding="utf-8"))
            voices = json.loads((folder / "voices.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(v): notes.guess_voice_name(str(v), presentes.get(str(v), "")) for v in voices}

    def _after_relabel(self) -> None:
        # mantém a MESMA nota aberta e re-renderiza: agora com os nomes na transcrição
        # e o ícone de vozes VERDE (a pendência foi resolvida).
        sel = self._selected()
        keep = sel[0] if sel else None
        self._current_key = None   # força re-render (agora com os nomes)
        self._refresh_list()
        if keep is not None:
            self._select_in_tree(keep)

    # -- "Minhas pendências" (#22) -------------------------------------------

    def _collect_action_groups(self) -> list[dict]:
        """Agrega a seção 'Pendências e Ações' de TODAS as reuniões, uma entrada por
        reunião: {dt, title, path, folder, client, items}. Ponto ÚNICO de origem dos
        dados do hub (#78) — a migração p/ o índice (#76) troca só aqui. Ordenado por
        data desc."""
        from .. import notes

        groups: list[dict] = []
        try:
            files = list(self._notes_dir().glob("*.md"))
        except OSError:
            files = []
        for f in files:
            try:
                md = f.read_text(encoding="utf-8")
            except OSError:
                continue
            items = notes.parse_action_items(md)
            if not items:
                continue
            dt, _dur, title = _note_info(f)
            groups.append({"dt": dt, "title": title or f.stem, "path": f,
                           "folder": self._recording_folder_for(f),
                           "client": _client_of(f), "items": items})
        groups.sort(key=lambda g: g["dt"], reverse=True)
        return groups

    def _open_action_items(self) -> None:
        # atalho secundário (ícone task-list do rodapé): abre o hub de 1ª classe (#78/#82)
        self.app.show_action_hub()

    def reveal_note(self, note_path) -> None:
        """Navega até a reunião de caminho `note_path` na árvore (público; usado
        pela capa ao clicar numa reunião recente). Aceita str ou Path."""
        self._reveal_note(Path(note_path))

    def reveal_note_at_section(self, note_path, section_title: str = "Pendências e Ações") -> None:
        """Abre a nota E rola o leitor até a seção `section_title` (default: pendências).
        Usado pelo hub (#78): clicar num item leva à nota já posicionada. Como o leitor
        usa setMarkdown (sem âncoras de header), reusa o mecanismo de find por texto
        (QTextCursor + ensureCursorVisible, padrão do _goto_hit)."""
        self.show()
        self.raise_()
        self._reveal_note(Path(note_path))   # seleciona na árvore → renderiza o markdown
        if not section_title:
            return
        doc = self._view.document()
        cur = doc.find(section_title)        # 1ª ocorrência do texto do header
        if not cur.isNull():
            self._view.setTextCursor(cur)
            self._view.ensureCursorVisible()

    def _reveal_note(self, note_path: Path) -> None:
        if self._select_in_tree(note_path):
            return
        for f in (self._search, self._f_participant, self._f_client, self._f_since, self._f_until):
            f.clear()
        self._refresh_list()
        self._select_in_tree(note_path)

    def _select_in_tree(self, note_path: Path) -> bool:
        for item, (p, _t, _s) in self._items.items():
            if p == note_path:
                self._tree.setCurrentItem(item)
                self._tree.scrollToItem(item)
                self.raise_()
                return True
        return False

    # -- janela --------------------------------------------------------------

    def show(self) -> None:  # noqa: A003
        super().show()
        self.raise_()
        self.activateWindow()
        if not self._titlebar_done:
            self._titlebar_done = True
            widgets.enable_dark_titlebar(self)
        self._ensure_index_async()
        self._refresh_list()

    def _ensure_index_async(self) -> None:
        def work() -> None:
            try:
                from .. import meetings_index

                meetings_index.reindex_if_needed(self.app.cfg.output.resolved_recordings_dir())
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def hide(self) -> None:  # noqa: A003
        self._poll.stop()
        super().hide()

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()


def _is_blocking(label: str) -> bool:
    up = (label or "").upper()
    return "BLOQUEANTE" in up or "BLOCK" in up


def _label_rank(label: str) -> int:
    """Severidade do rótulo p/ a ordenação 'por rótulo' (bloqueantes no topo)."""
    up = (label or "").upper()
    if _is_blocking(label):
        return 0
    if "ABERTO" in up or "OPEN" in up:
        return 1
    return 2 if (label or "").strip() else 3


# filtros de estado do hub (chip -> rótulo). "Ativas" é o default.
_HUB_FILTERS = (
    ("active", "Ativas"),
    ("blocking", "Bloqueantes"),
    ("done", "Resolvidas"),
    ("dismissed", "Dispensadas"),
    ("archived", "Arquivadas"),
)


class _ActionItemsWindow(QWidget):
    """Hub de pendências (#78): agrega a seção 'Pendências e Ações' de TODAS as reuniões,
    com filtros (ativas/bloqueantes/resolvidas/dispensadas/arquivadas), por cliente, busca
    e ordenação; agrupa por reunião (título · data · cliente); rótulos como chips coloridos;
    clique no item abre a nota já rolada na seção. Estado em .actions.json por pasta
    (fonte da verdade); navegação delegada à janela de Notas. Instância única do app."""

    def __init__(self, app, collect, on_reveal_section):
        super().__init__()
        self.app = app
        self._collect = collect                       # callable -> list[dict] de grupos
        self._on_reveal_section = on_reveal_section    # callable(path) -> navega até a seção
        self._groups: list[dict] = []
        self._filter = "active"
        self._focus_path = None
        self._headers: dict[str, QLabel] = {}
        self.setWindowTitle("ScribaDev — Pendências")
        self.setMinimumSize(600, 460)
        widgets.remember_geometry(self, "action_hub", default=(160, 120, 660, 560))
        self._build()

    # -- construção ----------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        head = QHBoxLayout()
        title = QLabel("Pendências"); title.setStyleSheet("font-size:15pt; font-weight:bold;")
        head.addWidget(title)
        self._count = QLabel(""); self._count.setProperty("role", "muted")
        head.addWidget(self._count)
        head.addStretch(1)
        self._archive_btn = QPushButton("Arquivar antigas")
        self._archive_btn.setCursor(Qt.PointingHandCursor)
        self._archive_btn.clicked.connect(self._archive_old)
        head.addWidget(self._archive_btn)
        root.addLayout(head)

        chips = QHBoxLayout(); chips.setSpacing(6)
        self._chip_group = QButtonGroup(self); self._chip_group.setExclusive(True)
        self._chips: dict[str, QPushButton] = {}
        for key, label in _HUB_FILTERS:
            b = QPushButton(label); b.setCheckable(True); b.setCursor(Qt.PointingHandCursor)
            b.setObjectName("hubChip")
            b.clicked.connect(lambda _c=False, k=key: self._set_filter(k))
            self._chip_group.addButton(b)
            self._chips[key] = b
            chips.addWidget(b)
        self._chips["active"].setChecked(True)
        chips.addStretch(1)
        root.addLayout(chips)

        tools = QHBoxLayout(); tools.setSpacing(8)
        self._client = QComboBox(); widgets.no_wheel_steal(self._client)
        self._client.currentIndexChanged.connect(lambda _=0: self._render())
        tools.addWidget(QLabel("Cliente:")); tools.addWidget(self._client)
        self._sort = QComboBox(); widgets.no_wheel_steal(self._sort)
        self._sort.addItems(["Data (recente)", "Rótulo"])
        self._sort.currentIndexChanged.connect(lambda _=0: self._render())
        tools.addWidget(QLabel("Ordenar:")); tools.addWidget(self._sort)
        self._search = QLineEdit(); self._search.setClearButtonEnabled(True)
        self._search.setPlaceholderText("Buscar nos itens…")
        self._search.textChanged.connect(lambda _=0: self._render())
        tools.addWidget(self._search, 1)
        root.addLayout(tools)

        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        root.addWidget(self._scroll, 1)
        self._style_controls()

    def _style_controls(self) -> None:
        # QSS por objectName (#hubChip): scoped, não vaza border p/ filhos (gotcha #70).
        t = theme.active()
        for b in self._chips.values():
            b.setStyleSheet(
                f"QPushButton#hubChip {{ padding:3px 12px; border-radius:{t.radius}px;"
                f" border:1px solid {t.border}; background:{t.field}; color:{t.muted}; }}"
                f"QPushButton#hubChip:checked {{ background:{t.accent}; color:{t.on_accent};"
                f" border-color:{t.accent}; }}")

    # -- dados / filtros -----------------------------------------------------
    def refresh(self) -> None:
        """(Re)coleta os grupos da origem única e re-renderiza (após marcar/dispensar/
        arquivar, ou ao abrir)."""
        try:
            self._groups = self._collect() if self._collect else []
        except Exception:
            log.exception("falha ao coletar pendências do hub")
            self._groups = []
        self._populate_clients()
        self._render()

    def _populate_clients(self) -> None:
        cur = self._client.currentText() if self._client.count() else ""
        clients = sorted({(g["client"] or "").strip() for g in self._groups
                          if (g["client"] or "").strip()})
        self._client.blockSignals(True)
        self._client.clear()
        self._client.addItem("Todos os clientes")
        self._client.addItems(clients)
        i = self._client.findText(cur)
        self._client.setCurrentIndex(i if i > 0 else 0)
        self._client.blockSignals(False)

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._render()

    def _passes_filter(self, state: str, label: str) -> bool:
        f = self._filter
        if f == "active":
            return state == "open"
        if f == "blocking":
            return state == "open" and _is_blocking(label)
        return state == f   # done / dismissed / archived

    def focus_meeting(self, note_path) -> None:
        """Rola até a reunião de `note_path` (abertura via app.show_action_hub(note_path))."""
        self._focus_path = str(note_path) if note_path else None
        self._render()

    # -- render --------------------------------------------------------------
    def _render(self) -> None:
        from .. import notes

        self._headers = {}
        client_sel = self._client.currentText() if self._client.currentIndex() > 0 else ""
        needle = self._search.text().strip().lower()
        by_label = self._sort.currentIndex() == 1
        visible: list[tuple[dict, list]] = []
        for g in self._groups:
            if client_sel and (g["client"] or "").strip() != client_sel:
                continue
            state_map = notes.load_action_state(g["folder"])
            items = []
            for it in g["items"]:
                st = state_map.get(it["key"], "open")
                if not self._passes_filter(st, it.get("label", "")):
                    continue
                if needle and needle not in (
                        f"{it.get('text', '')} {it.get('raw', '')} {it.get('label', '')}".lower()):
                    continue
                items.append((it, st))
            if not items:
                continue
            if by_label:
                items.sort(key=lambda pair: _label_rank(pair[0].get("label", "")))
            visible.append((g, items))
        if by_label:
            visible.sort(key=lambda gi: min(_label_rank(it.get("label", "")) for it, _ in gi[1]))

        body = QWidget(); lay = QVBoxLayout(body)
        shown_total = 0
        for g, items in visible:
            self._add_group_header(lay, g)
            for it, st in items:
                self._add_item(lay, g, it, st)
                shown_total += 1
        if shown_total == 0:
            empty = QLabel(self._empty_text()); empty.setProperty("role", "muted")
            lay.addWidget(empty)
        lay.addStretch(1)
        self._count.setText(f"{shown_total} item(ns)")
        self._scroll.setWidget(body)
        if self._focus_path is not None:
            hdr = self._headers.get(self._focus_path)
            if hdr is not None:
                self._scroll.ensureWidgetVisible(hdr)
            self._focus_path = None

    def _empty_text(self) -> str:
        return {
            "active": "Nenhuma pendência ativa. ✓",
            "blocking": "Nenhuma pendência bloqueante.",
            "done": "Nada resolvido ainda.",
            "dismissed": "Nada dispensado.",
            "archived": "Nada arquivado.",
        }.get(self._filter, "Nada por aqui.")

    def _add_group_header(self, lay, g: dict) -> None:
        t = theme.active()
        bits = [g["title"], f"{g['dt']:%d/%m %H:%M}"]
        if (g["client"] or "").strip():
            bits.append(g["client"].strip())
        hdr = QLabel("  ·  ".join(bits))
        hdr.setStyleSheet(f"color:{t.accent_hover}; font-weight:bold; font-size:{t.font_size_small + 1}pt;")
        hdr.setCursor(Qt.PointingHandCursor)
        hdr.mousePressEvent = lambda _e, p=g["path"]: self._on_reveal_section(p)
        lay.addWidget(hdr)
        self._headers[str(g["path"])] = hdr

    def _label_chip(self, label: str) -> QLabel:
        t = theme.active()
        up = (label or "").upper()
        if _is_blocking(label):
            bg, fg = t.rec, t.on_accent
        elif "ABERTO" in up or "OPEN" in up:
            bg, fg = t.warn, t.on_highlight
        else:
            bg, fg = t.field, t.muted
        word = label.split()[0].upper() if label.split() else ""
        chip = QLabel(word); chip.setObjectName("hubLabelChip")
        chip.setStyleSheet(f"QLabel#hubLabelChip {{ background:{bg}; color:{fg};"
                           f" border-radius:9px; padding:1px 8px; font-size:{t.font_size_small}pt; }}")
        return chip

    def _add_item(self, lay, g: dict, item: dict, state: str) -> None:
        from .. import notes

        t = theme.active()
        row = QWidget()
        rl = QHBoxLayout(row); rl.setContentsMargins(14, 1, 4, 1); rl.setSpacing(8)
        cb = widgets.AnimatedCheckBox()
        cb.setFixedWidth(cb._BOX + 8)   # indicador só: área de clique disjunta do texto
        cb.setChecked(state == "done")

        def toggle(on, folder=g["folder"], k=item["key"]) -> None:
            notes.set_action_done(folder, k, on)
            self._render()

        cb.toggled.connect(toggle)
        rl.addWidget(cb, 0, Qt.AlignTop)
        if item.get("label"):
            rl.addWidget(self._label_chip(item["label"]), 0, Qt.AlignTop)
        txt = QLabel(item.get("text") or item.get("raw") or "")
        txt.setWordWrap(True)
        txt.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        if state == "done":
            txt.setStyleSheet(f"color:{t.muted}; text-decoration:line-through;")
        elif state in ("dismissed", "archived"):
            txt.setStyleSheet(f"color:{t.muted};")
        txt.setCursor(Qt.PointingHandCursor)
        txt.mousePressEvent = lambda _e, p=g["path"]: self._on_reveal_section(p)
        widgets.add_tooltip(txt, item.get("raw") or item.get("text") or "")
        rl.addWidget(txt, 1)
        if state in ("dismissed", "archived"):
            rl.addWidget(widgets.icon_button(
                "refresh", "Reabrir (voltar para ativa)",
                lambda folder=g["folder"], k=item["key"]: self._set_state(folder, k, "open"),
                size=15), 0, Qt.AlignTop)
        else:
            rl.addWidget(widgets.icon_button(
                "dismiss", "Dispensar (falso-positivo)",
                lambda folder=g["folder"], k=item["key"]: self._set_state(folder, k, "dismissed"),
                size=15), 0, Qt.AlignTop)
        lay.addWidget(row)

    def _set_state(self, folder, key: str, state: str) -> None:
        from .. import notes

        notes.set_action_state(folder, key, state)
        self._render()

    def _archive_old(self) -> None:
        from datetime import datetime, timedelta

        from .. import notes

        try:
            days = int(self.app.cfg.ui.pending_window_days)
        except Exception:
            days = 30
        if days <= 0:
            QMessageBox.information(self, "Arquivar antigas",
                                    "O recorte está desligado (0 dias). Ajuste em Configurações.")
            return
        cutoff = datetime.now() - timedelta(days=days)
        pending = 0
        for g in self._groups:
            if g["dt"] >= cutoff:
                continue
            st = notes.load_action_state(g["folder"])
            pending += sum(1 for it in g["items"] if st.get(it["key"], "open") == "open")
        if pending == 0:
            QMessageBox.information(self, "Arquivar antigas",
                                    f"Nenhuma pendência ativa em reuniões com mais de {days} dias.")
            return
        resp = QMessageBox.question(
            self, "Arquivar antigas",
            f"Arquivar {pending} pendência(s) de reuniões com mais de {days} dias?\n"
            "Ficam recuperáveis no filtro Arquivadas.")
        if resp != QMessageBox.Yes:
            return
        meetings = [{"export_path": str(g["path"]), "folder": str(g["folder"]),
                     "started_at": g["dt"].isoformat()} for g in self._groups]
        n = notes.archive_old_action_items(meetings, older_than_days=days)
        self.refresh()
        widgets.flash_button(self._archive_btn, f"{n} arquivada(s)", "Arquivar antigas")

    # -- janela --------------------------------------------------------------
    def restyle_theme(self) -> None:
        """Troca de tema a quente (#70/#78): re-aplica os estilos inline (chips, cabeçalhos,
        rótulos, strikethrough) re-renderizando. Chamado por theme._restyle_top_levels."""
        self._style_controls()
        self._render()

    def show(self) -> None:  # noqa: A003
        super().show()
        self.raise_()
        self.activateWindow()
        widgets.enable_dark_titlebar(self)

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()


def _clip(text: str) -> None:
    from PySide6.QtWidgets import QApplication

    QApplication.clipboard().setText(text)


# --------------------------------------------------------------- harness ------

def main() -> int:
    import sys

    from PySide6.QtWidgets import QApplication

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = QApplication(sys.argv)
    theme.apply(app)

    class _FakeOut:
        def __init__(self, base):
            self._base = base

        def resolved_export_dir(self):
            return self._base

        def resolved_recordings_dir(self):
            return self._base / "_rec"

    import tempfile

    base = Path(tempfile.mkdtemp(prefix="scriba_qt_notes_"))
    (base / "2026-07-02_15-30_Boleto.md").write_text(
        "---\ntitulo: Boleto não gera\ndata: 2026-07-02T15:30:00\ncliente: ACME\n---\n\n"
        "## Objetivo\nInvestigar o boleto.\n\n## Participantes\n- **Participante 1**: fala do financeiro\n\n"
        "## Pendências e Ações\n- [ ] Corrigir CNPJ alfanumérico\n\n"
        "## Transcrição completa\nfala 1\nfala 2\n", encoding="utf-8")

    class _FakeApp:
        cfg = type("C", (), {"output": _FakeOut(base)})()

        def ui(self, fn): fn()

    win = NotesWindow(_FakeApp())
    win.resize(980, 620)
    win.show()
    return app.exec()


if __name__ == "__main__":
    import os

    rc = main()
    logging.shutdown()
    os._exit(rc)
