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
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QSplitter,
    QTextBrowser,
    QTextEdit,
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
        split.setSizes([300, 620])

        self._search_timer = QTimer(self); self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(350); self._search_timer.timeout.connect(self._refresh_list)
        self._poll = QTimer(self); self._poll.setInterval(1500); self._poll.timeout.connect(self._poll_progress)
        self._find_timer = QTimer(self); self._find_timer.setSingleShot(True)
        self._find_timer.setInterval(200); self._find_timer.timeout.connect(self._run_find)
        self._refresh_sig.connect(self._refresh_list)
        self._apply_theme()

    # -- construção da UI ----------------------------------------------------

    def _build_left(self) -> QWidget:
        left = QWidget()
        lay = QVBoxLayout(left); lay.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Reuniões"); title.setStyleSheet("font-weight:bold;")
        lay.addWidget(title)

        self._search = widgets.make_entry("filtrar notas (assunto, participante…)")
        self._search.textChanged.connect(lambda: self._search_timer.start())
        lay.addWidget(self._search)

        self._f_participant = widgets.make_entry("participante")
        self._f_client = widgets.make_entry("cliente")
        for f in (self._f_participant, self._f_client):
            f.textChanged.connect(lambda: self._search_timer.start())
            lay.addWidget(f)
        self._f_since = widgets.DateFilter("de")
        self._f_until = widgets.DateFilter("até")
        for f in (self._f_since, self._f_until):
            f.changed.connect(lambda: self._search_timer.start())
            lay.addWidget(f)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self._tree.itemSelectionChanged.connect(self._show_selected)
        lay.addWidget(self._tree, 1)

        actions = QHBoxLayout()
        actions.addWidget(widgets.ModernButton("Atualizar", self._refresh_list))
        actions.addWidget(widgets.ModernButton("Abrir pasta", self._open_notes_dir))
        actions.addWidget(widgets.ModernButton("Pendências", self._open_action_items))
        actions.addStretch(1)
        lay.addLayout(actions)
        return left

    def _build_right(self) -> QWidget:
        right = QWidget()
        lay = QVBoxLayout(right); lay.setContentsMargins(12, 0, 0, 0)

        header = QHBoxLayout()
        self._title = widgets.make_entry("título da reunião")
        header.addWidget(self._title, 1)
        cli = QLabel("Cliente:"); cli.setProperty("role", "muted")
        header.addWidget(cli)
        self._client = widgets.make_entry("empresa")
        self._client.setFixedWidth(150)
        header.addWidget(self._client)
        self._save_btn = widgets.ModernButton("Salvar", self._save_header)
        header.addWidget(self._save_btn)
        lay.addLayout(header)

        acts = QHBoxLayout()
        self._prompt_btn = widgets.ModernButton("Gerar Prompt de Contexto", self._copy_prompt, kind="primary")
        self._tr_btn = widgets.ModernButton("Copiar transcrição", self._copy_transcript)
        self._chat_btn = widgets.ModernButton("Perguntar à reunião", self._open_chat)
        self._voice_btn = widgets.ModernButton("Rotular vozes…", self._open_speaker_labeler)
        self._voice_btn.setVisible(False)   # só quando a gravação tem vozes (voices.json)
        self._delete_btn = widgets.ModernButton("Excluir nota…", self._ask_delete)
        for b in (self._prompt_btn, self._tr_btn, self._chat_btn, self._voice_btn):
            acts.addWidget(b)
        acts.addStretch(1)
        acts.addWidget(self._delete_btn)
        lay.addLayout(acts)

        # Presentes (colapsável)
        self._presentes = _Collapsible("Presentes")
        lay.addWidget(self._presentes)

        # find bar (visível só com nota aberta)
        self._find_bar = QWidget()
        fl = QHBoxLayout(self._find_bar); fl.setContentsMargins(0, 0, 0, 0)
        self._find = widgets.make_entry("buscar nesta nota…")
        self._find.textChanged.connect(lambda: self._find_timer.start())
        self._find.returnPressed.connect(self._next_hit)
        fl.addWidget(self._find, 1)
        fl.addWidget(widgets.ModernButton("▲", self._prev_hit))
        fl.addWidget(widgets.ModernButton("▼", self._next_hit))
        self._find_count = QLabel(""); self._find_count.setProperty("role", "muted")
        self._find_count.setFixedWidth(48)
        fl.addWidget(self._find_count)
        self._find_transcript = widgets.AnimatedCheckBox("incluir transcrição")
        self._find_transcript.toggled.connect(self._on_toggle_transcript)
        fl.addWidget(self._find_transcript)
        lay.addWidget(self._find_bar)

        # leitor de markdown
        self._view = QTextBrowser()
        self._view.setOpenExternalLinks(True)
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
            self._delete_btn.setVisible(False)
            self._voice_btn.setVisible(False)
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
                icon = "⚠" if status in ("failed", "no_audio") else "⏳"
                item = QTreeWidgetItem(day_node, [f"{dt:%H:%M}  {icon} {util.stage_label(status)}"])
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
        self._delete_btn.setVisible(False)
        self._voice_btn.setVisible(False)
        self._title.setText("")
        self._client.setText("")
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
        self._title.setText(title or "")
        self._client.setText(_client_of(path))
        self._delete_btn.setVisible(True)
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
        """Mostra o resumo; se há transcrição, um rótulo no fim permite expandi-la
        (colapsada por padrão, como na versão tk)."""
        md = summary_md
        if self._transcript_md and not self._transcript_shown:
            md += "\n\n---\n*Transcrição completa oculta — marque \"incluir transcrição\" para vê-la.*"
        elif self._transcript_md and self._transcript_shown:
            md += f"\n\n## Transcrição completa\n\n{self._transcript_md}"
        self._view.setMarkdown(md)

    def _on_toggle_transcript(self, checked: bool) -> None:
        self._transcript_shown = checked
        sel = self._selected_md()
        if sel is not None:
            summary, self._transcript_md = _summary_and_transcript(sel)
            self._render_view(summary)
            self._run_find()

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

    def _copy_prompt(self) -> None:
        md = self._selected_md()
        if md is None:
            return
        from .. import context_prompt

        _clip(context_prompt.build_context_prompt(md))
        widgets.flash_button(self._prompt_btn, "✓ Copiado", "Gerar Prompt de Contexto")

    def _copy_transcript(self) -> None:
        md = self._selected_md()
        if md is None:
            return
        _summary, tr = _summary_and_transcript(md)
        if not tr:
            widgets.flash_button(self._tr_btn, "Sem transcrição", "Copiar transcrição")
            return
        _clip(tr)
        widgets.flash_button(self._tr_btn, "✓ Copiado", "Copiar transcrição")

    def _open_chat(self) -> None:
        md = self._selected_md()
        if md is None:
            widgets.flash_button(self._chat_btn, "Selecione uma nota", "Perguntar à reunião")
            return
        from .chat_ui import ChatWindow

        summary, transcript = _summary_and_transcript(md)
        self._chat = ChatWindow(summary, transcript, self._title.text().strip() or "reunião")
        self._chat.show()

    def _save_header(self) -> None:
        sel = self._selected()
        if not sel or sel[2] is not None:
            return
        path = sel[0]
        new_title = self._title.text().strip()
        new_client = self._client.text().strip()
        from ..notes import set_note_client, set_note_title

        if new_title:
            set_note_title(path, new_title)
        set_note_client(path, new_client)
        self._refresh_list()
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

    def _run_find(self) -> None:
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
            self._goto_hit()
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
        from .speakers_ui import has_labelable_voices

        try:
            has = has_labelable_voices(self._recording_folder_for(note_path))
        except Exception:
            has = False
        self._voice_btn.setVisible(has)

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
        self._current_key = None   # força re-render (agora com os nomes)
        self._refresh_list()

    # -- "Minhas pendências" (#22) -------------------------------------------

    def _collect_action_groups(self) -> list:
        from .. import notes

        groups = []
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
            groups.append((dt, title or f.stem, f, self._recording_folder_for(f), items))
        groups.sort(key=lambda g: g[0], reverse=True)
        return groups

    def _open_action_items(self) -> None:
        self._actions_win = _ActionItemsWindow(self._collect_action_groups(), self._reveal_note)
        self._actions_win.show()

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


class _ActionItemsWindow(QWidget):
    """"Minhas pendências": agrega a seção 'Pendências e Ações' de TODAS as reuniões,
    com checkbox por item (estado em .actions.json por pasta) e clique no título → nota."""

    def __init__(self, groups: list, on_reveal):
        super().__init__()
        self._groups = groups
        self._on_reveal = on_reveal
        self.setWindowTitle("ScribaDev — Minhas pendências")
        self.setMinimumSize(560, 400)
        root = QVBoxLayout(self)
        head = QHBoxLayout()
        title = QLabel("Minhas pendências"); title.setStyleSheet("font-size:15pt; font-weight:bold;")
        head.addWidget(title)
        self._count = QLabel(""); self._count.setProperty("role", "muted")
        head.addWidget(self._count); head.addStretch(1)
        self._show_done = widgets.AnimatedCheckBox("mostrar resolvidas")
        self._show_done.toggled.connect(self._render)
        head.addWidget(self._show_done)
        root.addLayout(head)
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        root.addWidget(self._scroll, 1)
        self._render()

    def _render(self) -> None:
        from .. import notes

        body = QWidget()
        lay = QVBoxLayout(body)
        open_n = done_n = 0
        any_visible = False
        for dt, title, md_path, folder, items in self._groups:
            state = notes.load_action_state(folder)
            shown = [it for it in items if self._show_done.isChecked() or not state.get(it["key"])]
            done_n += sum(1 for it in items if state.get(it["key"]))
            open_n += sum(1 for it in items if not state.get(it["key"]))
            if not shown:
                continue
            any_visible = True
            hdr = QLabel(f"{title}  ·  {dt:%d/%m %H:%M}")
            hdr.setStyleSheet(f"color:{theme.active().accent_hover}; font-weight:bold; font-size:11pt;")
            hdr.setCursor(Qt.PointingHandCursor)
            hdr.mousePressEvent = lambda _e, p=md_path: self._on_reveal(p)
            lay.addWidget(hdr)
            for it in shown:
                self._add_item(lay, folder, it, bool(state.get(it["key"])))
        if not any_visible:
            lay.addWidget(QLabel("Nenhuma reunião tem pendências." if not self._groups
                                 else 'Tudo resolvido! Marque "mostrar resolvidas" para revê-las.'))
        lay.addStretch(1)
        self._count.setText(f"{open_n} aberta(s) · {done_n} resolvida(s)")
        self._scroll.setWidget(body)

    def _add_item(self, lay, folder, item, done) -> None:
        from .. import notes

        text = (f"[{item['label']}] " if item.get("label") else "") + (item.get("text") or item.get("raw") or "")
        cb = widgets.AnimatedCheckBox(text)
        cb.setChecked(done)
        if done:
            cb.setStyleSheet(f"color:{theme.active().muted}; text-decoration:line-through;")

        def toggle(on, f=folder, k=item["key"]) -> None:
            notes.set_action_done(f, k, on)
            self._render()

        cb.toggled.connect(toggle)
        lay.addWidget(cb)

    def show(self) -> None:  # noqa: A003
        super().show()
        self.raise_()
        self.activateWindow()
        widgets.enable_dark_titlebar(self)


class _Collapsible(QWidget):
    """Cabeçalho clicável (▸/▾) + conteúdo colapsável."""

    def __init__(self, title: str):
        super().__init__()
        self._open = False
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 6); lay.setSpacing(2)
        self._hdr = QLabel()
        self._hdr.setCursor(Qt.PointingHandCursor)
        self._hdr.setStyleSheet(f"color:{theme.active().accent_hover}; font-weight:bold; font-size:11pt;")
        self._hdr.mousePressEvent = lambda _e: self._toggle()
        lay.addWidget(self._hdr)
        self._content: QWidget | None = None
        self._lay = lay
        self._title = title
        self._render_hdr()

    def set_header(self, title: str) -> None:
        self._title = title
        self._render_hdr()

    def set_content(self, w: QWidget) -> None:
        if self._content is not None:
            self._content.deleteLater()
        self._content = w
        self._lay.addWidget(w)
        w.setVisible(self._open)

    def _render_hdr(self) -> None:
        self._hdr.setText(f"{'▾' if self._open else '▸'} {self._title}")

    def _toggle(self) -> None:
        self._open = not self._open
        self._render_hdr()
        if self._content is not None:
            self._content.setVisible(self._open)


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
