"""Janela de Notas do ScribaDev: reuniões por dia, leitor de markdown e progresso ao vivo.

Janela própria (botão "Notas" do dashboard) — separada das Configurações. Lista as
notas prontas e as reuniões ainda em processamento (com barra animada), busca por
conteúdo, título editável e botão "Gerar Prompt de Contexto".
"""

from __future__ import annotations

import re
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import ttk

from . import mdview, util
from .widgets import (
    FONT,
    FONT_BOLD,
    PALETTE,
    LinkLabel,
    ModernButton,
    enable_dark_titlebar,
    make_entry,
    style_notebook,
)

_BG = PALETTE["bg"]


class NotesWindow:
    _FRONT_DATA = re.compile(r"^data:\s*(.+)$", re.M)
    _FRONT_DUR = re.compile(r"^duracao_minutos:\s*(\d+)", re.M)
    _FRONT_TITLE = re.compile(r"^titulo:\s*(.+)$", re.M)
    # [ \t]* (e não \s*): com "cliente:" vazio, \s* atravessaria o \n e capturaria a linha seguinte
    _FRONT_CLIENT = re.compile(r"^cliente:[ \t]*(.+)$", re.M)
    _NAME_DT = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})")

    def __init__(self, root: tk.Misc, app):
        self.app = app
        self._titlebar_done = False
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.title("ScribaDev — Notas")
        self.win.configure(bg=_BG)
        self.win.attributes("-alpha", 0.97)
        self.win.minsize(860, 540)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        # o auto-poll morre quando a janela sai de "visível" (minimizada/oculta);
        # voltar a aparecer (restaurar/deiconify) religa a lista na hora — sem isso
        # a janela mostrava estágios congelados ("Gerando resumo…" eterno)
        self.win.bind("<Map>", self._on_map)
        style_notebook(self.win)  # tema escuro de Treeview/Scrollbar (estilos globais)
        try:
            self.win.iconbitmap(str(util.ICON_ICO))
        except Exception:
            pass

        body = tk.Frame(self.win, bg=_BG, padx=14, pady=12)
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg=_BG)
        left.pack(side="left", fill="y")
        tk.Label(left, text="Reuniões", bg=_BG, fg=PALETTE["text"], font=FONT_BOLD).pack(anchor="w")

        # busca por conteúdo
        search_row = tk.Frame(left, bg=_BG)
        search_row.pack(fill="x", pady=(6, 0))
        self.search_var = tk.StringVar()
        make_entry(search_row, self.search_var, width=22).pack(side="left", fill="x", expand=True, ipady=3)
        LinkLabel(search_row, "✕", self._clear_search).pack(side="left", padx=(6, 0))
        self._search_job = None
        self.search_var.trace_add("write", lambda *a: self._debounced_search())

        self.notes_tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.notes_tree.column("#0", width=320)
        self.notes_tree.pack(fill="y", expand=True, pady=(6, 6))
        self.notes_tree.bind("<<TreeviewSelect>>", lambda e: self._show_selected_note())
        actions = tk.Frame(left, bg=_BG)
        actions.pack(fill="x")
        LinkLabel(actions, "Atualizar", self._refresh_notes_list).pack(side="left")
        tk.Label(actions, text="·", bg=_BG, fg=PALETTE["muted"]).pack(side="left", padx=4)
        LinkLabel(actions, "Abrir pasta", self._open_notes_dir).pack(side="left")

        right = tk.Frame(body, bg=_BG)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        # título e cliente editáveis (o cliente vem identificado pela IA; vazio = digite)
        title_row = tk.Frame(right, bg=_BG)
        title_row.pack(fill="x", pady=(0, 6))
        self.note_title_var = tk.StringVar()
        make_entry(title_row, self.note_title_var, width=30).pack(side="left", fill="x", expand=True, ipady=4)
        tk.Label(title_row, text="Cliente:", bg=_BG, fg=PALETTE["muted"], font=FONT).pack(side="left", padx=(10, 4))
        self.note_client_var = tk.StringVar()
        make_entry(title_row, self.note_client_var, width=14).pack(side="left", ipady=4)
        ModernButton(title_row, "Salvar", self._save_note_header).pack(side="right", padx=(8, 0))

        # cópias independentes: contexto da atividade (sem transcrição) e transcrição;
        # cada tabela da nota tem seu próprio "⧉ copiar (Excel)" ao lado dela
        copy_row = tk.Frame(right, bg=_BG)
        copy_row.pack(fill="x", pady=(0, 8))
        self.copy_btn = ModernButton(copy_row, "Gerar Prompt de Contexto", self._copy_context_prompt,
                                     kind="primary", width=200)
        self.copy_btn.pack(side="left")
        self.copy_tr_btn = ModernButton(copy_row, "Copiar transcrição", self._copy_transcript, width=150)
        self.copy_tr_btn.pack(side="left", padx=(8, 0))
        # rotular vozes (#1): só aparece quando a reunião selecionada tem diarização
        # (voices.json na pasta da gravação) — empacotado sob demanda
        self.label_voices_btn = ModernButton(copy_row, "Rotular vozes…", self._open_speaker_labeler, width=130)

        # área de conteúdo: alterna entre o leitor (markdown) e a barra de progresso
        self.view_frame = tk.Frame(right, bg=_BG)
        self.note_view = tk.Text(self.view_frame)
        mdview.setup_view(self.note_view)
        sb = ttk.Scrollbar(self.view_frame, orient="vertical", command=self.note_view.yview)
        self.note_view.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.note_view.pack(side="left", fill="both", expand=True)
        self.view_frame.pack(fill="both", expand=True)

        # painel de progresso (reunião em processamento)
        ttk.Style().configure(
            "Scriba.Horizontal.TProgressbar",
            troughcolor=PALETTE["field"], background=PALETTE["accent"],
            borderwidth=0, thickness=14,
        )
        self.progress_frame = tk.Frame(right, bg=_BG)
        self.progress_stage = tk.Label(self.progress_frame, text="", bg=_BG,
                                       fg=PALETTE["text"], font=("Segoe UI", 14, "bold"))
        self.progress_stage.pack(anchor="w", pady=(48, 12))
        self.progress_bar = ttk.Progressbar(self.progress_frame, style="Scriba.Horizontal.TProgressbar",
                                            mode="indeterminate", length=380)
        self.progress_bar.pack(anchor="w", fill="x")
        self.progress_hint = tk.Label(self.progress_frame, text="", bg=_BG, fg=PALETTE["muted"],
                                      font=FONT, justify="left", wraplength=440)
        self.progress_hint.pack(anchor="w", pady=(16, 0))

        # item -> (caminho_ou_pasta, título, status)  · status None = nota pronta
        self._note_items: dict[str, tuple[Path, str | None, str | None]] = {}
        self._poll_job = None
        self._pending_sig: dict[str, str] = {}
        self._current_view_key: Path | None = None
        self._showing_note = False
        self._progress_animating = False

    # -- lista ----------------------------------------------------------------

    def _on_map(self, event) -> None:
        """Janela voltou a ficar visível: relê a lista e religa o auto-poll."""
        if event.widget is self.win:  # <Map> também chega dos widgets filhos
            self._refresh_notes_list()

    def _clear_search(self) -> None:
        self.search_var.set("")

    def _debounced_search(self) -> None:
        if self._search_job:
            self.win.after_cancel(self._search_job)
        self._search_job = self.win.after(350, self._refresh_notes_list)

    def _notes_dir(self) -> Path:
        d = self.app.cfg.output.resolved_export_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _open_notes_dir(self) -> None:
        util.open_path(self._notes_dir())

    def _note_info(self, path: Path) -> tuple[datetime, int | None, str | None]:
        """(data/hora, duração, título) da nota: frontmatter > nome do arquivo > mtime."""
        dur, title, dt = None, None, None
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:500]
            m = self._FRONT_DUR.search(head)
            if m:
                dur = int(m.group(1))
            m = self._FRONT_TITLE.search(head)
            if m:
                title = m.group(1).strip()
            m = self._FRONT_DATA.search(head)
            if m:
                dt = datetime.fromisoformat(m.group(1).strip())
        except (OSError, ValueError):
            pass
        if dt is None:
            m = self._NAME_DT.search(path.name)
            if m:
                y, mo, d, h, mi = map(int, m.groups())
                dt = datetime(y, mo, d, h, mi)
            else:
                dt = datetime.fromtimestamp(path.stat().st_mtime)
        return dt, dur, title

    @staticmethod
    def _group_label(d: date) -> str:
        today = date.today()
        prefix = "Hoje — " if d == today else ("Ontem — " if d == today - timedelta(days=1) else "")
        return f"{prefix}{d.strftime('%d/%m/%Y')}"

    def _pending_meetings(self) -> list[tuple[datetime, str, Path, str | None]]:
        """Reuniões em processamento OU com falha (pasta de gravações): (dt, status, pasta, título)."""
        import json

        out = []
        try:
            # rglob: árvore ano/mês/dia + pastas legadas (planas)
            metas = list(self.app.cfg.output.resolved_recordings_dir().rglob("meta.json"))
        except OSError:
            return out
        for mp in metas:
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            # falhas aparecem na lista (com ⚠) em vez de sumirem em silêncio
            if meta.get("status") not in util.IN_PROGRESS_STATUSES + ("failed", "no_audio"):
                continue
            folder = mp.parent
            started = meta.get("started_at", "")
            try:
                dt = datetime.fromisoformat(started) if started else datetime.fromtimestamp(mp.stat().st_mtime)
            except (ValueError, OSError):
                dt = datetime.now()
            out.append((dt, meta["status"], folder, meta.get("title")))
        return out

    def _refresh_notes_list(self) -> None:
        self._search_job = None
        query = self.search_var.get().strip().lower()
        try:
            files = list(self._notes_dir().glob("*.md"))
        except OSError:
            files = []
        entries: list[tuple[datetime, str, tuple]] = []
        for f in files:
            if query:
                try:
                    if query not in f.read_text(encoding="utf-8", errors="replace").lower():
                        continue
                except OSError:
                    continue
            dt, dur, title = self._note_info(f)
            entries.append((dt, "note", (f, dur, title)))
        # reuniões em andamento só aparecem fora de busca (ainda não têm conteúdo)
        pending = [] if query else self._pending_meetings()
        for dt, status, folder, title in pending:
            entries.append((dt, "pending", (folder, status, title)))
        entries.sort(key=lambda x: x[0], reverse=True)
        # assinatura por caminho completo: com a árvore ano/mês/dia, só o nome
        # ("16-34") colidiria entre dias diferentes
        self._pending_sig = {str(f): st for _dt, st, f, _t in pending}

        # preserva seleção e dias expandidos entre reconstruções
        sel = self.notes_tree.selection()
        selected_key = self._note_items[sel[0]][0] if sel and sel[0] in self._note_items else None
        open_days = {
            self.notes_tree.item(d, "text")
            for d in self.notes_tree.get_children() if self.notes_tree.item(d, "open")
        }

        self.notes_tree.delete(*self.notes_tree.get_children())
        self._note_items.clear()
        if not entries:
            msg = (
                f'Nenhuma nota contém "{query}".' if query
                else "Nenhuma nota ainda — elas aparecem aqui depois da primeira reunião."
            )
            self._show_note_pane()
            mdview.render(self.note_view, msg)
            self._schedule_poll(True)  # segue vigiando: uma call nova deve aparecer sozinha
            return

        current_day, day_node, first_day = None, None, True
        select_item = None
        for dt, kind, payload in entries:
            if dt.date() != current_day:
                current_day = dt.date()
                label_day = self._group_label(current_day)
                day_node = self.notes_tree.insert(
                    "", "end", text=label_day,
                    open=first_day or bool(query) or label_day in open_days,
                )
                first_day = False
            if kind == "note":
                path, dur, title = payload
                if title:
                    short = title if len(title) <= 36 else title[:35] + "…"
                    label = f"{dt:%H:%M}  {short}"
                else:
                    label = dt.strftime("%H:%M:%S") + (f"   ·  {dur} min" if dur else "")
                item = self.notes_tree.insert(day_node, "end", text=label)
                self._note_items[item] = (path, title, None)
                key = path
            else:  # pending ou falha
                folder, status, title = payload
                icon = "⚠" if status in ("failed", "no_audio") else "⏳"
                label = f"{dt:%H:%M}  {icon} {util.stage_label(status)}"
                item = self.notes_tree.insert(day_node, "end", text=label)
                self._note_items[item] = (folder, title, status)
                key = folder
            if select_item is None or key == selected_key:
                select_item = item
        if select_item:
            self.notes_tree.selection_set(select_item)
            self.notes_tree.see(select_item)
        # poll sempre ativo enquanto a janela está visível: além do progresso das
        # pendentes, é o que faz uma call NOVA aparecer com a janela já aberta
        self._schedule_poll(True)

    # -- progresso de reuniões em andamento ----------------------------------

    def _schedule_poll(self, active: bool) -> None:
        # não checa visibilidade aqui (show() agenda antes do deiconify); quem
        # se protege é o _poll_progress, que para sozinho se a janela sumir
        if self._poll_job:
            self.win.after_cancel(self._poll_job)
            self._poll_job = None
        if active:
            self._poll_job = self.win.after(1500, self._poll_progress)

    def _poll_progress(self) -> None:
        """Vigia em segundo plano: estágio mudou, nota ficou pronta ou call nova apareceu.

        Para sozinho quando a janela deixa de estar visível (minimizada/oculta) —
        o <Map> religa tudo quando ela volta.
        """
        self._poll_job = None
        if not self.win.winfo_viewable():
            return
        cur = {str(f): st for _dt, st, f, _t in self._pending_meetings()}
        if cur != self._pending_sig:
            self._refresh_notes_list()  # estágio mudou (ou virou nota): reconstrói
            return
        self._poll_job = self.win.after(1500, self._poll_progress)

    def _show_progress_pane(self) -> None:
        if self.view_frame.winfo_ismapped():
            self.view_frame.pack_forget()
        if not self.progress_frame.winfo_ismapped():
            self.progress_frame.pack(fill="both", expand=True)

    def _show_note_pane(self) -> None:
        self._stop_progress_anim()
        if self.progress_frame.winfo_ismapped():
            self.progress_frame.pack_forget()
        if not self.view_frame.winfo_ismapped():
            self.view_frame.pack(fill="both", expand=True)

    def _stop_progress_anim(self) -> None:
        if self._progress_animating:
            self.progress_bar.stop()
            self._progress_animating = False

    def _render_progress(self, folder: Path, status: str) -> None:
        self._show_progress_pane()
        if self.label_voices_btn.winfo_ismapped():
            self.label_voices_btn.pack_forget()  # reunião em andamento: nada a rotular
        self._showing_note = False
        self._current_view_key = folder
        self.note_title_var.set("")
        self.note_client_var.set("")
        self.progress_stage.configure(text=util.stage_label(status))
        if status in ("failed", "no_audio"):
            self._stop_progress_anim()
            if status == "no_audio":
                hint = "Os arquivos de áudio desta gravação estão vazios — nada chegou do microfone/loopback."
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
                hint += f"\n\nDetalhes em process.log na pasta da gravação. Para tentar de novo:\nscribadev process \"{folder}\""
            self.progress_hint.configure(text=hint)
            return
        if not self._progress_animating:
            self.progress_bar.start(12)  # barra animada (atividade); o estágio mostra o ponto exato
            self._progress_animating = True
        self.progress_hint.configure(text=(
            "A reunião está sendo processada localmente. O conteúdo aparece aqui assim que "
            "ficar pronto — pode fechar esta janela, o processamento continua em segundo plano."
        ))

    # -- visualização ----------------------------------------------------------

    def _show_selected_note(self) -> None:
        sel = self.notes_tree.selection()
        if not sel or sel[0] not in self._note_items:
            return  # clicou num nó de data
        path, title, status = self._note_items[sel[0]]
        if status is not None:
            self._render_progress(path, status)
            return
        self.note_title_var.set(title or "")
        self.note_client_var.set(self._client_of(path))
        self._update_voice_button(path)
        # mesma nota já renderizada: não re-renderiza (preserva a rolagem durante o poll)
        if self._showing_note and self._current_view_key == path:
            return
        self._show_note_pane()
        try:
            # transcrição fechada por padrão: o que importa é a especificação
            mdview.render(self.note_view, path.read_text(encoding="utf-8"),
                          collapsed={"Transcrição completa"})
        except OSError as e:
            mdview.render(self.note_view, f"# Erro\n\nNão consegui ler {path.name}: {e}")
            return
        self._current_view_key = path
        self._showing_note = True
        self._highlight_hits()

    # -- cópias independentes ---------------------------------------------------

    _TRANSCRIPT_TITLE = "transcrição completa"

    def _selected_md(self) -> str | None:
        """Markdown da nota selecionada (None se nada/pendente/ilegível)."""
        sel = self.notes_tree.selection()
        if not sel or sel[0] not in self._note_items:
            return None
        path, _title, status = self._note_items[sel[0]]
        if status is not None:
            return None  # ainda processando — nada para copiar
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _set_clip(self, content: str) -> None:
        self.win.clipboard_clear()
        self.win.clipboard_append(content)

    def _flash(self, btn, text: str, original: str) -> None:
        btn.set_text(text)
        self.win.after(1500, lambda: btn.set_text(original))

    @staticmethod
    def _strip_frontmatter(md: str) -> str:
        lines = md.splitlines()
        if lines and lines[0].strip() == "---":
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    return "\n".join(lines[i + 1:]).strip()
        return md.strip()

    def _transcript_only(self, md: str) -> str | None:
        _pre, secs = mdview.split_sections(self._strip_frontmatter(md))
        for title, text in secs:
            if title.strip().lower() == self._TRANSCRIPT_TITLE and text.strip():
                return text.strip()
        return None

    def _copy_context_prompt(self) -> None:
        """Gera o Prompt de Contexto (issue #3) da nota selecionada e copia.

        Reembala o resumo numa moldura que faz o Claude Code absorver a call como
        contexto do gap em tratamento. A mesma função (context_prompt, pura) será
        reusada pelo Scriba nuvem."""
        md = self._selected_md()
        if md is None:
            return
        from . import context_prompt

        self._set_clip(context_prompt.build_context_prompt(md))
        self._flash(self.copy_btn, "✓ Copiado", "Gerar Prompt de Contexto")

    def _copy_transcript(self) -> None:
        md = self._selected_md()
        if md is None:
            return
        tr = self._transcript_only(md)
        if tr is None:
            self._flash(self.copy_tr_btn, "Sem transcrição", "Copiar transcrição")
            return
        self._set_clip(tr)
        self._flash(self.copy_tr_btn, "✓ Copiado", "Copiar transcrição")

    def _recording_folder_for(self, note_path: Path) -> Path:
        """Pasta da gravação correspondente à nota exportada.

        Nota "2026-06-12_11-55[_2]_reuniao.md" → gravações\\2026\\06\\12\\11-55[_2]
        (com ou sem "_Título" anexado ao nome) ou o legado plano
        gravações\\2026-06-12_11-55[_2]. Em caso de ambiguidade no mesmo minuto,
        decide pelo export_path do meta.json.
        """
        import json

        rec_dir = self.app.cfg.output.resolved_recordings_dir()
        stem = note_path.stem.replace("_reuniao", "")
        legacy = rec_dir / stem
        if legacy.exists():
            return legacy
        m = self._NAME_DT.search(stem)
        if not m:
            return legacy
        y, mo, d, h, mi = m.groups()
        suffix = stem[m.end():]  # "_2" de gravações no mesmo minuto
        exact = rec_dir / y / mo / d / f"{h}-{mi}{suffix}"
        if exact.exists():
            return exact
        day_dir = rec_dir / y / mo / d
        if day_dir.is_dir():
            for cand in sorted(day_dir.glob(f"{h}-{mi}*")):  # renomeada com título
                try:
                    exp = json.loads((cand / "meta.json").read_text(encoding="utf-8")).get("export_path", "")
                except (OSError, ValueError):
                    continue
                if exp and Path(exp).name == note_path.name:
                    return cand
        return exact

    def _client_of(self, path: Path) -> str:
        """Cliente da nota (linha `cliente:` do frontmatter), ou ""."""
        try:
            m = self._FRONT_CLIENT.search(path.read_text(encoding="utf-8", errors="replace")[:500])
        except OSError:
            return ""
        return m.group(1).strip() if m else ""

    def _save_note_header(self) -> None:
        """Aplica título e cliente editados à nota exportada e à cópia na gravação."""
        sel = self.notes_tree.selection()
        if not sel or sel[0] not in self._note_items:
            return
        path, _old, status = self._note_items[sel[0]]
        if status is not None:
            return  # reunião ainda em processamento: sem cabeçalho para editar
        new_title = self.note_title_var.get().strip()
        new_client = self.note_client_var.get().strip()  # vazio remove o cliente
        from .notes import set_note_client, set_note_title

        rec_folder = self._recording_folder_for(path)
        if new_title:  # título vazio não apaga (segue a regra de sempre)
            set_note_title(path, new_title)
            set_note_title(rec_folder / "notas.md", new_title)
        set_note_client(path, new_client)
        set_note_client(rec_folder / "notas.md", new_client)
        # espelha no meta.json, se existir
        meta_path = rec_folder / "meta.json"
        if meta_path.exists():
            try:
                import json

                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if new_title:
                    meta["title"] = new_title
                meta["client"] = new_client
                util.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
            except (OSError, ValueError):
                pass
        if new_title:
            util.rename_recording_folder(rec_folder, new_title)  # pasta acompanha o título
        self._showing_note = False  # força re-render com o novo cabeçalho
        self._refresh_notes_list()

    # -- rotulagem de vozes (#1) -----------------------------------------------

    def _update_voice_button(self, note_path: Path) -> None:
        """Mostra "Rotular vozes…" só se a gravação desta nota tem vozes (voices.json)."""
        from .speakers_ui import has_labelable_voices

        try:
            has = has_labelable_voices(self._recording_folder_for(note_path))
        except Exception:
            has = False
        if has and not self.label_voices_btn.winfo_ismapped():
            self.label_voices_btn.pack(side="left", padx=(8, 0))
        elif not has and self.label_voices_btn.winfo_ismapped():
            self.label_voices_btn.pack_forget()

    def _open_speaker_labeler(self) -> None:
        sel = self.notes_tree.selection()
        if not sel or sel[0] not in self._note_items:
            return
        path, _title, status = self._note_items[sel[0]]
        if status is not None:
            return  # reunião em andamento: sem vozes a rotular ainda
        from .speakers_ui import label_speakers_dialog

        label_speakers_dialog(self.win, self._recording_folder_for(path), on_saved=self._after_relabel)

    def _after_relabel(self) -> None:
        """Pós-rotulagem: re-renderiza a nota (agora com os nomes) e a lista."""
        self._showing_note = False
        self._refresh_notes_list()

    def _highlight_hits(self) -> None:
        """Marca as ocorrências da busca na nota renderizada e rola até a primeira."""
        query = self.search_var.get().strip()
        t = self.note_view
        t.tag_configure("hit", background="#6e3a3a", foreground="#ffffff")
        t.tag_remove("hit", "1.0", "end")
        if not query:
            return
        mdview.expand_all(t)  # ocorrência pode estar numa seção fechada (ex.: transcrição)
        idx, first = "1.0", None
        while True:
            idx = t.search(query, idx, nocase=1, stopindex="end")
            if not idx:
                break
            end = f"{idx}+{len(query)}c"
            t.tag_add("hit", idx, end)
            first = first or idx
            idx = end
        if first:
            t.see(first)

    # -- janela -----------------------------------------------------------------

    def show(self) -> None:
        # mapeia antes de renderizar: a largura real da janela dimensiona as tabelas
        self.win.deiconify()
        if not self._titlebar_done:
            self._titlebar_done = True
            enable_dark_titlebar(self.win)
        self.win.update_idletasks()
        self._refresh_notes_list()
        self.win.lift()
        self.win.focus_force()

    def hide(self) -> None:
        self._stop_progress_anim()
        self.win.withdraw()
