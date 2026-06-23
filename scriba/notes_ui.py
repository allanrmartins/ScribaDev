"""Janela de Notas do ScribaDev: reuniões por dia, leitor de markdown e progresso ao vivo.

Janela própria (botão "Notas" do dashboard) — separada das Configurações. Lista as
notas prontas e as reuniões ainda em processamento (com barra animada), busca por
conteúdo, título editável e botão "Gerar Prompt de Contexto".
"""

from __future__ import annotations

import logging
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
    CalendarPopup,
    LinkLabel,
    ModernButton,
    add_placeholder,
    enable_dark_titlebar,
    make_entry,
    mask_date_br,
    style_notebook,
)

_BG = PALETTE["bg"]
log = logging.getLogger("scriba.notes_ui")


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

        # busca da ESQUERDA: localiza a NOTA (título + resumo + participantes). NÃO
        # destaca dentro da nota — isso é a barra de find da direita (self.find_bar).
        search_row = tk.Frame(left, bg=_BG)
        search_row.pack(fill="x", pady=(6, 0))
        self.search_var = tk.StringVar()
        se = make_entry(search_row, self.search_var, width=22)
        se.pack(side="left", fill="x", expand=True, ipady=3)
        add_placeholder(se, self.search_var, "filtrar notas (assunto, participante…)")
        LinkLabel(search_row, "✕", self._clear_search).pack(side="left", padx=(6, 0))

        # filtros estruturados (#11) — usam o índice de busca
        self.filter_participant_var = tk.StringVar()
        self.filter_client_var = tk.StringVar()
        self.filter_since_var = tk.StringVar()
        self.filter_until_var = tk.StringVar()
        filt = tk.Frame(left, bg=_BG)
        filt.pack(fill="x", pady=(4, 0))

        def _frow(label, var, hint, width=12):
            r = tk.Frame(filt, bg=_BG)
            r.pack(fill="x", pady=(2, 0))
            tk.Label(r, text=label, bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
                     width=9, anchor="w").pack(side="left")
            e = make_entry(r, var, width=width)
            e.pack(side="left", fill="x", expand=True, ipady=2)
            add_placeholder(e, var, hint)
            return e

        _frow("participante", self.filter_participant_var, "nome")
        _frow("cliente", self.filter_client_var, "empresa")
        tk.Label(filt, text="período", bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
                 anchor="w").pack(fill="x", pady=(4, 0))

        def _date_row(label, var):
            # período: só data — máscara DD/MM/AAAA (texto descartado) + datepicker 📅
            r = tk.Frame(filt, bg=_BG)
            r.pack(fill="x", pady=(2, 0))
            tk.Label(r, text=label, bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
                     width=4, anchor="w").pack(side="left")
            e = make_entry(r, var, width=10)
            e.pack(side="left", fill="x", expand=True, ipady=2)
            add_placeholder(e, var, "DD/MM/AAAA")
            mask_date_br(e, var)
            LinkLabel(r, "📅", lambda: self._open_calendar(e, var)).pack(side="left", padx=(4, 0))

        _date_row("de", self.filter_since_var)
        _date_row("até", self.filter_until_var)

        self._search_job = None
        for _v in (self.search_var, self.filter_participant_var, self.filter_client_var,
                   self.filter_since_var, self.filter_until_var):
            _v.trace_add("write", lambda *a: self._debounced_search())

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
        # excluir nota (#16): ação destrutiva — fica à direita, separada das ações
        # seguras, e só aparece com uma nota aberta (done). Sempre pede confirmação.
        self.delete_btn = ModernButton(copy_row, "Excluir nota…", self._ask_delete_note, width=120)

        # painel "Presentes" colapsável (abaixo dos botões): o palpite da IA de quem é
        # cada voz, lido do resumo. Só aparece quando a nota tem a seção Participantes.
        self.presentes_panel = tk.Frame(right, bg=_BG)
        self._presentes_open = False
        self._presentes_desc_labels: list[tk.Label] = []
        self.presentes_panel.bind("<Configure>", self._reflow_presentes)

        # área de conteúdo: alterna entre o leitor (markdown) e a barra de progresso
        self.view_frame = tk.Frame(right, bg=_BG)
        self.note_view = tk.Text(self.view_frame)
        mdview.setup_view(self.note_view)
        sb = ttk.Scrollbar(self.view_frame, orient="vertical", command=self.note_view.yview)
        self.note_view.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.note_view.pack(side="left", fill="both", expand=True)
        self.view_frame.pack(fill="both", expand=True)

        # barra de busca DENTRO da nota (fixa, acima do leitor; só visível com nota
        # aberta). Independente da busca da esquerda: destaca e navega no texto.
        self.find_bar = tk.Frame(right, bg=_BG)
        self.find_var = tk.StringVar()
        fe = make_entry(self.find_bar, self.find_var, width=20)
        fe.pack(side="left", fill="x", expand=True, ipady=2)
        add_placeholder(fe, self.find_var, "buscar nesta nota…")
        fe.bind("<Return>", lambda e: self._next_hit())        # Enter: próxima
        fe.bind("<KP_Enter>", lambda e: self._next_hit())
        fe.bind("<Shift-Return>", lambda e: self._prev_hit())  # Shift+Enter: anterior
        fe.bind("<Shift-KP_Enter>", lambda e: self._prev_hit())
        fe.bind("<Escape>", lambda e: self._reset_find())
        self._find_entry = fe
        LinkLabel(self.find_bar, "▲", self._prev_hit).pack(side="left", padx=(6, 0))
        LinkLabel(self.find_bar, "▼", self._next_hit).pack(side="left", padx=(2, 0))
        self.find_count_var = tk.StringVar(value="")
        tk.Label(self.find_bar, textvariable=self.find_count_var, bg=_BG, fg=PALETTE["muted"],
                 font=("Segoe UI", 8), width=6, anchor="w").pack(side="left", padx=(6, 0))
        self.find_transcript_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            self.find_bar, text="incluir transcrição", variable=self.find_transcript_var,
            bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8), activebackground=_BG,
            activeforeground=PALETTE["text"], selectcolor=PALETTE["field"], bd=0,
            highlightthickness=0, takefocus=False,
        ).pack(side="left", padx=(8, 0))
        self.find_var.trace_add("write", lambda *a: self._debounced_find())
        self.find_transcript_var.trace_add("write", lambda *a: self._run_find())
        self.win.bind("<Control-f>", lambda e: self._focus_find())

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
        self._hits: list = []  # (start, end) de cada ocorrência da busca interna
        self._hit_idx = -1     # ocorrência "atual"; Enter avança (circular)
        self._find_job = None  # debounce da busca interna

    # -- lista ----------------------------------------------------------------

    def _on_map(self, event) -> None:
        """Janela voltou a ficar visível: relê a lista e religa o auto-poll."""
        if event.widget is self.win:  # <Map> também chega dos widgets filhos
            self._refresh_notes_list()

    def _clear_search(self) -> None:
        for v in (self.search_var, self.filter_participant_var, self.filter_client_var,
                  self.filter_since_var, self.filter_until_var):
            v.set("")

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
        q = self.search_var.get().strip()
        participant = self.filter_participant_var.get().strip()
        client = self.filter_client_var.get().strip()
        since = self.filter_since_var.get().strip()
        until = self.filter_until_var.get().strip()
        searching = bool(q or participant or client or since or until)
        entries: list[tuple[datetime, str, tuple]] = []
        if searching:
            # busca pelo índice (#11): full-text (resumo+transcrição) + filtros.
            # Pendentes não entram (ainda sem conteúdo p/ buscar).
            entries.extend(self._index_search(q, participant, client, since, until))
            pending: list = []
        else:
            try:
                files = list(self._notes_dir().glob("*.md"))
            except OSError:
                files = []
            for f in files:
                dt, dur, title = self._note_info(f)
                entries.append((dt, "note", (f, dur, title)))
            pending = self._pending_meetings()
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
                "Nenhuma reunião encontrada para a busca/filtros." if searching
                else "Nenhuma nota ainda — elas aparecem aqui depois da primeira reunião."
            )
            self._show_note_pane()
            self._hide_find_bar()
            if self.delete_btn.winfo_ismapped():
                self.delete_btn.pack_forget()
            if self.label_voices_btn.winfo_ismapped():
                self.label_voices_btn.pack_forget()
            self.presentes_panel.pack_forget()
            self._showing_note = False  # msg na área da nota: força re-render ao voltar
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
                    open=first_day or searching or label_day in open_days,
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

    # -- busca pelo índice (#11) ----------------------------------------------

    def _open_calendar(self, anchor: tk.Widget, var: tk.StringVar) -> None:
        """Abre o datepicker ancorado no campo; ao escolher, preenche DD/MM/AAAA."""
        s = var.get().strip()
        try:
            cur = datetime.strptime(s, "%d/%m/%Y").date() if len(s) == 10 else None
        except ValueError:
            cur = None
        CalendarPopup(anchor, cur, lambda d: var.set(d.strftime("%d/%m/%Y")))

    def _index_search(self, q: str, participant: str, client: str,
                      since: str, until: str) -> list:
        """Consulta o índice de busca (#10/#11) e devolve entradas 'note' apontando
        para o .md exportado: (dt, 'note', (path, dur_min, title)). Se o índice ainda
        não foi construído (o reindex de boot/abertura roda em thread), cai na
        varredura de texto p/ a busca de texto não aparecer vazia."""
        from . import meetings_index

        out: list = []
        try:
            if meetings_index.count() == 0:
                return self._bruteforce_text(q) if q else []
            since_iso, until_iso = util.date_range_filter(since, until)
            results = meetings_index.search(
                query=q or None, participant=participant or None, client=client or None,
                since=since_iso, until=until_iso, status="done", limit=1000,
                include_transcript=False,  # localizador de nota: transcrição é da busca interna
            )
        except Exception:
            return self._bruteforce_text(q) if q else []
        for r in results:
            exp = r.get("export_path")
            if not exp:
                continue
            path = Path(exp)
            if not path.exists():
                continue  # nota apagada à mão; o índice se cura no próximo reindex
            started = r.get("started_at") or ""
            try:
                dt = datetime.fromisoformat(started) if started else self._note_info(path)[0]
            except ValueError:
                dt = self._note_info(path)[0]
            secs = r.get("duration_s")
            dur = int(secs) // 60 if secs else None
            out.append((dt, "note", (path, dur, r.get("title"))))
        return out

    def _bruteforce_text(self, q: str) -> list:
        """Fallback (índice ainda vazio): varre os .md por substring, só no resumo
        (corta na transcrição) — localizador de nota."""
        ql = q.lower()
        out: list = []
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
                text = text[:i]  # localizador: só o resumo
            if ql not in text.lower():
                continue
            dt, dur, title = self._note_info(f)
            out.append((dt, "note", (f, dur, title)))
        return out

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
        if self.delete_btn.winfo_ismapped():
            self.delete_btn.pack_forget()  # reunião em andamento: não excluir em processamento
        self.presentes_panel.pack_forget()
        self._hide_find_bar()  # reunião em andamento: nada para buscar dentro
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
        if not self.delete_btn.winfo_ismapped():
            self.delete_btn.pack(side="right")
        # mesma nota já renderizada: não re-renderiza (preserva rolagem, Presentes e a
        # busca interna ativa durante o poll)
        if self._showing_note and self._current_view_key == path:
            return
        self._show_note_pane()
        try:
            md = path.read_text(encoding="utf-8")
        except OSError as e:
            self.presentes_panel.pack_forget()
            self._hide_find_bar()
            mdview.render(self.note_view, f"# Erro\n\nNão consegui ler {path.name}: {e}")
            return
        self._build_presentes_panel(md)  # painel Presentes colapsável acima do leitor
        # transcrição fechada por padrão: o que importa é a especificação
        mdview.render(self.note_view, md, collapsed={"Transcrição completa"})
        self._current_view_key = path
        self._showing_note = True
        self._show_find_bar()   # barra de busca dentro da nota (fixa)
        self._reset_find()      # nova nota → zera a busca interna

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

        folder = self._recording_folder_for(path)
        label_speakers_dialog(self.win, folder, on_saved=self._after_relabel,
                              guesses=self._voice_guesses(path, folder))

    def _voice_guesses(self, note_path: Path, folder: Path) -> dict[str, str]:
        """{rótulo da voz → palpite de nome} a partir da seção Presentes do resumo."""
        import json

        from . import notes

        try:
            presentes, _ = notes.parse_participants(note_path.read_text(encoding="utf-8"))
            voices = json.loads((folder / "voices.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(v): notes.guess_voice_name(str(v), presentes.get(str(v), "")) for v in voices}

    # -- exclusão de nota (#16) ------------------------------------------------

    def _ask_delete_note(self) -> None:
        """Confirmação + exclusão da nota selecionada (.md + índice; opção: gravação)."""
        sel = self.notes_tree.selection()
        if not sel or sel[0] not in self._note_items:
            return
        path, title, status = self._note_items[sel[0]]
        if status is not None:
            return  # reunião em processamento: não excluir (o worker pode estar ativo)
        self._confirm_delete_dialog(path, title)

    def _confirm_delete_dialog(self, note_path: Path, title: str | None) -> None:
        """Diálogo dark modal: confirma a exclusão e oferece apagar também a gravação."""
        folder = self._recording_folder_for(note_path)
        win = tk.Toplevel(self.win)
        win.withdraw()  # nasce oculta: posiciona ANTES de exibir (evita nascer fora e "pular")
        win.title("ScribaDev — Excluir nota")
        win.configure(bg=_BG, padx=22, pady=18)
        win.resizable(False, False)
        win.transient(self.win)
        try:
            win.iconbitmap(str(util.ICON_ICO))
        except Exception:
            pass

        tk.Label(win, text="Excluir nota?", bg=_BG, fg=PALETTE["accent"],
                 font=("Segoe UI", 12, "bold")).pack(anchor="w")
        nome = (title or note_path.stem.replace("_reuniao", "")).strip()
        if len(nome) > 64:
            nome = nome[:63] + "…"
        tk.Label(win, text=nome, bg=_BG, fg=PALETTE["text"], font=FONT_BOLD,
                 wraplength=380, justify="left").pack(anchor="w", pady=(2, 6))
        tk.Label(win, text="A nota sai da lista e do índice de busca. Esta ação não pode ser desfeita.",
                 bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
                 wraplength=380, justify="left").pack(anchor="w")

        also_audio = tk.BooleanVar(value=False)
        if folder.is_dir():
            tk.Checkbutton(
                win, text="Excluir também o áudio/gravação (sem volta)", variable=also_audio,
                bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8), activebackground=_BG,
                activeforeground=PALETTE["text"], selectcolor=PALETTE["field"], bd=0,
                highlightthickness=0, takefocus=False, anchor="w",
            ).pack(anchor="w", pady=(12, 0))
            tk.Label(win, text="Desmarcado, a gravação fica como backup (recuperável com “scribadev summarize”).",
                     bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
                     wraplength=380, justify="left").pack(anchor="w")

        btns = tk.Frame(win, bg=_BG)
        btns.pack(pady=(16, 2), anchor="e")

        def do_delete() -> None:
            win.destroy()
            self._do_delete(note_path, folder, bool(also_audio.get()))

        ModernButton(btns, "Cancelar", win.destroy, width=110).pack(side="left", padx=(0, 8))
        ModernButton(btns, "Excluir", do_delete, kind="primary", width=110).pack(side="left")

        win.bind("<Escape>", lambda e: win.destroy())
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        # centraliza sobre a janela de Notas AINDA OCULTA; reqwidth/reqheight valem antes
        # de exibir (winfo_width seria 1). Só então deiconify → já aparece no lugar certo.
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        px, py = self.win.winfo_rootx(), self.win.winfo_rooty()
        pw, ph = self.win.winfo_width(), self.win.winfo_height()
        win.geometry(f"+{px + max(0, (pw - w) // 2)}+{py + max(0, (ph - h) // 3)}")
        win.deiconify()
        enable_dark_titlebar(win)
        win.lift()
        win.focus_force()
        win.grab_set()  # modal: bloqueia a janela de Notas até decidir

    def _do_delete(self, note_path: Path, folder: Path, also_audio: bool) -> None:
        """Executa a exclusão: .md exportado + índice; opcionalmente a pasta de gravação.

        - sempre apaga o .md (a nota sai da lista) e tira a reunião do índice de busca;
        - mantendo a gravação (default): tombstone `.deleted` na pasta p/ o reindex não
          ressuscitar a nota; áudio/meta seguem como backup (re-summarize recupera);
        - apagando a gravação: rmtree da pasta (respeita o .lock) + poda pastas vazias.
        """
        from . import meetings_index

        # 1) o .md exportado — fonte da nota no painel
        try:
            note_path.unlink(missing_ok=True)
        except OSError as e:
            log.warning("exclusão: falha ao remover %s: %s", note_path.name, e)

        # 2) a gravação + o índice de busca
        if also_audio and folder.is_dir() and not util.is_locked(folder):
            import shutil

            from . import retention
            meetings_index.remove_meeting(folder)  # antes do rmtree: índice sai mesmo se falhar
            try:
                shutil.rmtree(folder)
                retention._prune_empty_dirs(
                    self.app.cfg.output.resolved_recordings_dir(), folder.parent)
            except OSError as e:
                log.warning("exclusão: falha ao remover gravação %s: %s", folder.name, e)
        else:
            # mantém a gravação como backup (ou .lock ativo): tombstone p/ não voltar no reindex
            if also_audio and folder.is_dir():
                log.info("exclusão: .lock ativo em %s — mantendo a gravação", folder.name)
            meetings_index.mark_deleted(folder)

        # 3) a nota some na hora: re-render (a seleção cai p/ a 1ª nota ou msg de vazio)
        self._showing_note = False
        self._current_view_key = None
        self._refresh_notes_list()

    # -- painel "Presentes" colapsável -----------------------------------------

    def _build_presentes_panel(self, md: str) -> None:
        """Monta o painel Presentes (colapsável) com o palpite da IA de quem é cada
        voz, lido do resumo. Esconde-se quando a nota não tem a seção Participantes."""
        from . import notes

        for w in self.presentes_panel.winfo_children():
            w.destroy()
        self._presentes_desc_labels = []
        presentes, _menc = notes.parse_participants(md)
        if not presentes:
            self.presentes_panel.pack_forget()
            return
        self._presentes_n = len(presentes)
        # mesma fonte/cor dos títulos de seção da nota (## Objetivo etc. no mdview)
        self._presentes_hdr = tk.Label(self.presentes_panel, bg=_BG, fg=PALETTE["accent_hover"],
                                       font=("Segoe UI", 13, "bold"), cursor="hand2", anchor="w")
        self._presentes_hdr.pack(anchor="w")
        self._presentes_hdr.bind("<Button-1>", lambda e: self._toggle_presentes())
        self._set_presentes_header()
        content = tk.Frame(self.presentes_panel, bg=_BG)
        self._presentes_content = content
        for label, desc in presentes.items():
            blk = tk.Frame(content, bg=_BG)
            blk.pack(fill="x", anchor="w", pady=(3, 0))
            tk.Label(blk, text="• " + label, bg=_BG, fg=PALETTE["text"], font=FONT_BOLD,
                     anchor="w").pack(anchor="w")
            dl = tk.Label(blk, text=desc, bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
                          justify="left", anchor="w", wraplength=600)
            dl.pack(anchor="w", padx=(12, 0))
            self._presentes_desc_labels.append(dl)
        if self._presentes_open:
            content.pack(fill="x", pady=(4, 0))
        self.presentes_panel.pack(fill="x", pady=(0, 8), before=self.view_frame)
        self._reflow_presentes()

    def _set_presentes_header(self) -> None:
        arrow = "▾" if self._presentes_open else "▸"
        self._presentes_hdr.configure(
            text=f"{arrow} Presentes ({self._presentes_n}) — quem é cada voz, segundo a IA")

    def _toggle_presentes(self) -> None:
        self._presentes_open = not self._presentes_open
        self._set_presentes_header()
        if self._presentes_open:
            self._presentes_content.pack(fill="x", pady=(4, 0))
            self._reflow_presentes()
        else:
            self._presentes_content.pack_forget()

    def _reflow_presentes(self, event=None) -> None:
        """Ajusta o wrap das descrições à largura atual (responsivo, sem cortar)."""
        if not self._presentes_desc_labels:
            return
        w = event.width if event is not None else self.presentes_panel.winfo_width()
        wl = max(200, w - 24)
        if wl == getattr(self, "_presentes_wl", None):
            return
        self._presentes_wl = wl
        for lbl in self._presentes_desc_labels:
            lbl.configure(wraplength=wl)

    def _after_relabel(self) -> None:
        """Pós-rotulagem: re-renderiza a nota (agora com os nomes) e a lista."""
        self._showing_note = False
        self._refresh_notes_list()

    def _debounced_find(self) -> None:
        if self._find_job:
            self.win.after_cancel(self._find_job)
        self._find_job = self.win.after(200, self._run_find)

    def _run_find(self, *_a) -> None:
        """Busca DENTRO da nota aberta (find_bar): destaca tudo, conta N/M, rola até a
        1ª. Sem "incluir transcrição" busca só o resumo; com, descolapsa a transcrição e
        busca tudo — t.see só rola em texto não-elidido, por isso a transcrição precisa
        estar expandida para navegar até um match nela."""
        self._find_job = None
        query = self.find_var.get().strip()
        t = self.note_view
        # destaque forte: todas em amarelo; a atual em laranja com borda
        t.tag_configure("hit", background="#ffe14d", foreground="#161616")
        t.tag_configure("hit_current", background="#ff8c1a", foreground="#000000",
                        borderwidth=1, relief="solid")
        t.tag_raise("hit_current")  # a atual vence onde as duas tags se sobrepõem
        t.tag_remove("hit", "1.0", "end")
        t.tag_remove("hit_current", "1.0", "end")
        self._hits = []
        self._hit_idx = -1
        if not query:
            self.find_count_var.set("")
            return
        tr = mdview.section_head_index(t, "Transcrição completa")  # None se não houver
        if self.find_transcript_var.get():
            if tr:
                mdview.expand_section(t, "Transcrição completa")
            stop = "end"
        else:
            stop = tr or "end"  # só o resumo (antes da transcrição)
        idx = "1.0"
        while True:
            idx = t.search(query, idx, nocase=1, stopindex=stop)
            if not idx:
                break
            end = t.index(f"{idx}+{len(query)}c")
            t.tag_add("hit", idx, end)
            self._hits.append((idx, end))
            idx = end
        if self._hits:
            self._hit_idx = 0
            self._mark_current_hit()
        else:
            self.find_count_var.set("0/0")

    def _mark_current_hit(self) -> None:
        """Realça a ocorrência atual (self._hit_idx), rola até ela e atualiza 'N/M'."""
        t = self.note_view
        t.tag_remove("hit_current", "1.0", "end")
        if not self._hits:
            return
        start, end = self._hits[self._hit_idx]
        t.tag_add("hit_current", start, end)
        t.see(start)
        self.find_count_var.set(f"{self._hit_idx + 1}/{len(self._hits)}")

    def _next_hit(self) -> str:
        """Enter / ▼: próxima ocorrência (circular)."""
        if not self._hits:
            return "break"
        self._hit_idx = (self._hit_idx + 1) % len(self._hits)
        self._mark_current_hit()
        return "break"

    def _prev_hit(self) -> str:
        """Shift+Enter / ▲: ocorrência anterior (circular)."""
        if not self._hits:
            return "break"
        self._hit_idx = (self._hit_idx - 1) % len(self._hits)
        self._mark_current_hit()
        return "break"

    def _reset_find(self) -> str:
        """Zera a busca interna (nova nota / Esc): campo, hits, contador e realces."""
        self.find_var.set("")
        self._hits = []
        self._hit_idx = -1
        self.find_count_var.set("")
        self.note_view.tag_remove("hit", "1.0", "end")
        self.note_view.tag_remove("hit_current", "1.0", "end")
        return "break"

    def _show_find_bar(self) -> None:
        if not self.find_bar.winfo_ismapped():
            self.find_bar.pack(fill="x", pady=(0, 6), before=self.view_frame)

    def _hide_find_bar(self) -> None:
        self.find_bar.pack_forget()

    def _focus_find(self) -> str:
        """Ctrl+F: foca a barra de busca da nota (quando visível)."""
        if self.find_bar.winfo_ismapped():
            self._find_entry.focus_set()
        return "break"

    # -- janela -----------------------------------------------------------------

    def show(self) -> None:
        # mapeia antes de renderizar: a largura real da janela dimensiona as tabelas
        self.win.deiconify()
        if not self._titlebar_done:
            self._titlebar_done = True
            enable_dark_titlebar(self.win)
        self.win.update_idletasks()
        self._ensure_index_async()  # índice de busca pronto/atualizado (#11), em thread
        self._refresh_notes_list()
        self.win.lift()
        self.win.focus_force()

    def _ensure_index_async(self) -> None:
        """Ao abrir as Notas, garante o índice de busca pronto/atualizado (#11) numa
        thread — não bloqueia a UI; no-op se já populado."""
        import threading

        def work() -> None:
            try:
                from . import meetings_index

                meetings_index.reindex_if_needed(self.app.cfg.output.resolved_recordings_dir())
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def hide(self) -> None:
        self._stop_progress_anim()
        self.win.withdraw()
