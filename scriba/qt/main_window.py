"""Janela principal (capa) do ScribaDev em PySide6.

Rosto do app e antessala da tela de Notas (o carro-chefe, #56). Além do card de
call ao vivo e da gravação manual, a capa surfaça o que é ACIONÁVEL: as reuniões
recentes (clicáveis -> abrem a nota selecionada), um resumo de estatísticas e as
pendências abertas. O diagnóstico de Serviços foi rebaixado a uma seção
colapsável (`widgets.Collapsible`). Os dados vêm do disco/índice em thread
(`_collect_home`, como o `_collect_status`) e o render é marshalado via
`app.ui(...)`; nada bloqueia o `show()`.

Comportamento de janela "de verdade": o X tira da barra mas o app segue na
bandeja (closeEvent -> hide). Mesma API pública que `main.py` chama: `show()`,
`hide()`, `show_update(ver)`. O render de status (`_render_status`) segue
separado da coleta para o harness/testes alimentarem itens prontos.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, updates, util
from . import theme, widgets

log = logging.getLogger("scriba.qt.main_window")

_LEVELS = {"ok": "ok", "warn": "warn", "off": "muted"}  # nível -> token de cor
_RECENT_N = 5    # reuniões recentes mostradas na capa
_PENDING_N = 4   # pendências abertas mostradas na capa


def _fmt(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def _fmt_hours(seconds: float) -> str:
    """Duração total amigável: '9h42', '3h', '48min', '0min'."""
    s = max(0, int(seconds))
    h, m = s // 3600, s % 3600 // 60
    if h and m:
        return f"{h}h{m:02d}"
    return f"{h}h" if h else f"{m}min"


def _elide(text: str, limit: int) -> str:
    """Corta a string em `limit` chars com reticências (evita que títulos longos
    estourem a largura da capa; o guard de botão cortado não cobre QLabel)."""
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _group_by_note(items: list) -> list:
    """Agrupa itens de pendência por reunião (`note_path`), preservando a ordem de entrada.
    Devolve [(item_cabeça, [itens]), …] — o 1º item de cada grupo carrega o contexto
    (título/data/cliente) do cabeçalho. Evita repetir o subtítulo da reunião (#80)."""
    groups: list = []
    index: dict = {}
    for it in items:
        key = (it.get("note_path") or "").strip()
        if key not in index:
            index[key] = len(groups)
            groups.append((it, []))
        groups[index[key]][1].append(it)
    return groups


def _short_date(iso: str) -> str:
    """'YYYY-MM-DD…' -> 'dd/mm' (data curta do cabeçalho de grupo). '' se vazio/curto."""
    iso = (iso or "").strip()
    return f"{iso[8:10]}/{iso[5:7]}" if len(iso) >= 10 else ""


def _started_within(m: dict, cutoff_iso: str) -> bool:
    """True se a reunião começou em `cutoff_iso` ou depois (dentro do recorte da capa, #79).
    Data ausente/corrompida → False (vai p/ o backlog: degradação segura, nunca infla ativas)."""
    started = (m.get("started_at") or "").strip()
    return bool(started) and started >= cutoff_iso


def _dot(diameter: int = 10) -> QLabel:
    d = QLabel()
    d.setFixedSize(diameter, diameter)
    return d


def _paint_dot(dot: QLabel, hexcolor: str) -> None:
    r = dot.width() // 2
    dot.setStyleSheet(f"background:{hexcolor}; border-radius:{r}px;")


def _clear_layout(lay) -> None:
    while lay.count():
        child = lay.takeAt(0)
        w = child.widget()
        if w is not None:
            w.deleteLater()


class _ClickRow(QFrame):
    """Linha inteira clicável (item de reunião/pendência). QFrame#rowItem para o
    hover do QSS; não é um QAbstractButton (o guard de botão cortado só afere
    botões, e estes itens elidem o texto em vez de crescer)."""

    def __init__(self, on_click):
        super().__init__()
        self.setObjectName("rowItem")
        self._on = on_click
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._on:
            self._on()


class MainWindow(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._processing = False
        self._update_ver = None
        self._titlebar_done = False
        self._call_state_kind = "idle"   # idle | rec | call (p/ estilizar o card)
        self._home_data = None           # último payload de _render_home (re-tema #70)
        self._status_items = None        # últimos itens de _render_status (re-tema #70)

        self.setWindowTitle(f"ScribaDev v{__version__}")
        self.setMinimumSize(520, 560)
        widgets.remember_geometry(self, "qt_main", default=(200, 120, 580, 680))
        try:
            self.setWindowIcon(util_icon())
        except Exception:
            pass

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(0)

        self._build_header(root)
        self._build_update_bar(root)
        self._build_call_card(root)
        self._build_stats(root)

        # ---- corpo rolável (recentes + pendências + serviços) ----------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(0, 12, 0, 0)
        self._body.setSpacing(0)
        self._build_notes_card(self._body)
        self._build_pending(self._body)
        self._body.addStretch(1)
        self._build_services(self._body)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self._apply_theme()
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(1000)

    # ---- construção da UI ------------------------------------------------------

    def _build_header(self, root) -> None:
        head = QHBoxLayout()
        title = QLabel("ScribaDev")
        title.setStyleSheet("font-size:16pt; font-weight:bold;")
        head.addWidget(title)
        ver = QLabel(updates.build_string())
        ver.setProperty("role", "muted")
        ver.setStyleSheet("font-size:9pt;")
        head.addWidget(ver, 0, Qt.AlignBottom)
        head.addStretch(1)
        # Notas é o carro-chefe (#56): botão primário, destacado do Log.
        notes_btn = widgets.ModernButton("Notas", lambda: self.app.show_notes(), kind="primary")
        notes_btn.setIcon(theme.qicon("folder", color=theme.active().on_accent))
        head.addWidget(notes_btn)
        head.addWidget(widgets.ModernButton("Log", lambda: self.app.show_log()))
        head.addWidget(widgets.icon_button("settings", "Configurações", lambda: self.app.show_settings()))
        root.addLayout(head)

    def _build_update_bar(self, root) -> None:
        self._update_bar = QFrame()
        self._update_bar.setObjectName("updateBar")
        ub = QHBoxLayout(self._update_bar)
        ub.setContentsMargins(12, 8, 12, 8)
        self._update_lbl = QLabel("")
        self._update_btn = widgets.ModernButton("Atualizar", self._do_update, kind="primary")
        self._update_btn.setIcon(theme.qicon("arrow-upload", color=theme.active().on_accent))
        ub.addWidget(self._update_lbl)
        ub.addStretch(1)
        ub.addWidget(self._update_btn)
        self._update_bar.hide()
        root.addWidget(self._update_bar)
        root.addSpacing(8)

    def _build_call_card(self, root) -> None:
        self._card = QFrame()
        self._card.setObjectName("callCard")
        cv = QVBoxLayout(self._card)
        cv.setContentsMargins(16, 12, 16, 12)
        cv.setSpacing(6)
        state_row = QHBoxLayout()
        self._call_dot = _dot()
        self._call_state = QLabel("Nenhuma ligação em andamento")
        self._call_state.setProperty("role", "muted")
        state_row.addWidget(self._call_dot)
        state_row.addSpacing(7)
        state_row.addWidget(self._call_state)
        state_row.addStretch(1)
        self._rec_btn = widgets.ModernButton("Gravar agora", self._toggle_record, kind="primary")
        self._rec_btn.setIcon(theme.qicon("record", color=theme.active().on_accent))
        state_row.addWidget(self._rec_btn)
        cv.addLayout(state_row)
        # cronômetro grande: só aparece quando há call/gravação (idle = card compacto)
        self._call_timer = QLabel("—")
        self._call_timer.setStyleSheet("font-size:28pt; font-weight:bold;")
        self._call_timer.setVisible(False)
        cv.addWidget(self._call_timer)
        root.addWidget(self._card)

    def _build_stats(self, root) -> None:
        self._stats = QFrame()
        self._stats.setObjectName("statsBar")
        sl = QHBoxLayout(self._stats)
        sl.setContentsMargins(14, 9, 14, 9)
        sl.setSpacing(18)
        self._stat_meetings = self._stat_label("folder")
        self._stat_hours = self._stat_label("hourglass")
        self._stat_clients = self._stat_label("people")
        for w in (self._stat_meetings, self._stat_hours, self._stat_clients):
            sl.addWidget(w)
        sl.addStretch(1)
        root.addSpacing(10)
        root.addWidget(self._stats)

    def _stat_label(self, icon_name: str) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        ico = QLabel()
        ico.setPixmap(theme.qicon(icon_name, color=theme.active().muted).pixmap(14, 14))
        lay.addWidget(ico)
        val = QLabel("…")
        val.setTextFormat(Qt.RichText)
        lay.addWidget(val)
        w._val = val   # ref p/ atualizar no render
        return w

    def _build_notes_card(self, body) -> None:
        self._note_card = QFrame()
        self._note_card.setObjectName("noteCard")
        nl = QVBoxLayout(self._note_card)
        nl.setContentsMargins(13, 11, 13, 10)
        nl.setSpacing(6)
        hdr = QHBoxLayout()
        cap = QLabel("Notas · reuniões recentes")
        cap.setStyleSheet("font-weight:bold;")
        hdr.addWidget(cap)
        hdr.addStretch(1)
        open_btn = widgets.ModernButton("Abrir", lambda: self.app.show_notes())
        open_btn.setIcon(theme.qicon("chevron-right", color=theme.active().text))
        hdr.addWidget(open_btn)
        nl.addLayout(hdr)
        self._recent_box = QWidget()
        self._recent_lay = QVBoxLayout(self._recent_box)
        self._recent_lay.setContentsMargins(0, 0, 0, 0)
        self._recent_lay.setSpacing(2)
        nl.addWidget(self._recent_box)
        self._set_recent_placeholder("Carregando reuniões…")
        body.addWidget(self._note_card)

    def _build_pending(self, body) -> None:
        body.addSpacing(14)
        hdr = QHBoxLayout()
        cap = QLabel("Minhas pendências")
        cap.setStyleSheet("font-weight:bold;")
        hdr.addWidget(cap)
        self._pending_badge = QLabel("")
        self._pending_badge.setObjectName("badge")
        self._pending_badge.setVisible(False)
        hdr.addSpacing(7)
        hdr.addWidget(self._pending_badge)
        hdr.addStretch(1)
        self._pending_open = QLabel('<a href="#">abrir</a>')
        self._pending_open.setStyleSheet(f"color:{theme.active().muted};")
        self._pending_open.linkActivated.connect(lambda _=None: self._open_pending_hub())
        hdr.addWidget(self._pending_open)
        body.addLayout(hdr)
        body.addSpacing(4)
        self._pending_box = QWidget()
        self._pending_lay = QVBoxLayout(self._pending_box)
        self._pending_lay.setContentsMargins(0, 0, 0, 0)
        self._pending_lay.setSpacing(5)
        body.addWidget(self._pending_box)

    def _build_services(self, body) -> None:
        body.addSpacing(10)
        self._services = widgets.Collapsible("Serviços")
        self._rows = QWidget()
        self._rows_lay = QVBoxLayout(self._rows)
        self._rows_lay.setContentsMargins(0, 4, 0, 0)
        self._rows_lay.setSpacing(6)
        self._services.set_content(self._rows)
        body.addWidget(self._services)

    # ---- tema ------------------------------------------------------------------

    def _apply_theme(self) -> None:
        t = theme.active()
        self.setStyleSheet(
            f"QFrame#callCard, QFrame#updateBar, QFrame#statsBar {{ background:{t.surface};"
            f" border-radius:{t.radius + 2}px; }}"
            f"QFrame#noteCard {{ background:{t.surface}; border:1px solid {t.accent};"
            f" border-radius:{t.radius + 2}px; }}"
            f"QFrame#rowItem {{ border-radius:{t.radius_sm}px; }}"
            f"QFrame#rowItem:hover {{ background:{t.field}; }}"
            f"QLabel#badge {{ background:{t.warn}; color:{t.bg}; border-radius:9px;"
            f" padding:0 7px; font-weight:bold; }}"
        )

    def _style_call_card(self) -> None:
        """Borda do card de call conforme o estado (destaque só quando há call)."""
        t = theme.active()
        border = {"rec": t.rec, "call": t.warn}.get(self._call_state_kind)
        edge = f"border:1px solid {border};" if border else "border:1px solid transparent;"
        self._card.setStyleSheet(f"QFrame#callCard {{ background:{t.surface};"
                                 f" border-radius:{t.radius + 2}px; {edge} }}")

    def restyle_theme(self) -> None:
        """Re-aplica os estilos inline dependentes de tema após uma troca a quente (o
        QSS global já mudou): cards da capa (fundo + borda de acento), borda do card de
        call por estado e re-render das seções dinâmicas (recentes/pendências/serviços)
        com os dados em cache — cada item fixa a cor do tema na criação, então sem o
        re-render sobra a cor do tema anterior (borda laranja / cards escuros no claro).
        Chamado por theme._restyle_top_levels via o contrato `restyle_theme`. (#70)"""
        self._apply_theme()
        self._style_call_card()
        if self._home_data is not None:
            self._render_home(self._home_data)
        if self._status_items is not None:
            self._render_status(self._status_items)

    # ---- gravação --------------------------------------------------------------

    def _toggle_record(self) -> None:
        if self._processing:
            return
        if self.app.is_recording():
            self._processing = True
            self._rec_btn.setText("Processando…")
            self._rec_btn.setIcon(theme.qicon("hourglass", color=theme.active().on_accent))
            self._rec_btn.setEnabled(False)
            t = threading.Thread(target=self.app.stop_recording, kwargs={"keep": True}, daemon=True)
            t.start()
            self._await_processing(t)
        else:
            threading.Thread(target=self.app.start_recording, args=("manual",), daemon=True).start()

    def _await_processing(self, t: threading.Thread) -> None:
        if t.is_alive():
            QTimer.singleShot(150, lambda: self._await_processing(t))
            return
        self._processing = False
        self._rec_btn.setEnabled(True)
        self.refresh_home()   # nova reunião processada -> atualiza recentes/stats

    # ---- aviso de nova versão (#19) -------------------------------------------

    def show_update(self, ver: str) -> None:
        self._update_ver = ver
        self._update_lbl.setText(f"Nova versão v{ver} disponível")
        self._update_lbl.setStyleSheet(f"color:{theme.active().ok}; font-weight:bold;")
        self._update_btn.setText("Atualizar" if updates.is_git_install() else "Baixar")
        self._update_bar.show()

    def _do_update(self) -> None:
        if self.app.is_recording():
            self._update_lbl.setText("Pare a gravação antes de atualizar.")
            self._update_lbl.setStyleSheet(f"color:{theme.active().accent}; font-weight:bold;")
            return
        if updates.is_git_install():
            self._update_lbl.setText("Atualizando… (git pull)")
            self._update_lbl.setStyleSheet(f"color:{theme.active().muted};")
            self._update_btn.setText("…")
            self.app.apply_update(self._update_done)
        else:
            import webbrowser

            webbrowser.open(updates.download_url())

    def _update_done(self, ok: bool, msg: str) -> None:
        if ok:
            self._update_lbl.setText("✓ Atualizado — reiniciando o ScribaDev…")
            self._update_lbl.setStyleSheet(f"color:{theme.active().ok}; font-weight:bold;")
            self._update_btn.hide()
            QTimer.singleShot(1500, self.app.relaunch)
        else:
            self._update_lbl.setText("✗ " + msg)
            self._update_lbl.setStyleSheet(f"color:{theme.active().accent}; font-weight:bold;")
            self._update_btn.setText("Tentar de novo")

    # ---- tick ------------------------------------------------------------------

    def _tick(self) -> None:
        if not self.isVisible():
            return
        t = theme.active()
        if self.app.is_recording():
            app_name = self.app.current_call_app()
            self._call_state.setText(f"Gravando ({app_name or 'manual'})")
            self._call_timer.setText(_fmt(self.app.recording_duration()))
            self._call_timer.setVisible(True)
            self._set_rec_idle(True)
            _paint_dot(self._call_dot, t.rec)
            self._set_call_kind("rec")
        elif getattr(self.app, "call_active", False):
            app_name = self.app.current_call_app()
            self._call_state.setText(f"Em call ({app_name or '?'}) — sem gravar")
            self._call_timer.setText(_fmt(self.app.call_duration()))
            self._call_timer.setVisible(True)
            self._set_rec_idle(False)
            _paint_dot(self._call_dot, t.warn)
            self._set_call_kind("call")
        else:
            self._call_state.setText("Nenhuma ligação em andamento")
            self._call_timer.setText("—")
            self._call_timer.setVisible(False)
            self._set_rec_idle(False)
            _paint_dot(self._call_dot, t.muted)
            self._set_call_kind("idle")
        news = getattr(self.app, "update_news", None)
        if news and self._update_bar.isHidden():
            self.show_update(news)

    def _set_call_kind(self, kind: str) -> None:
        if kind != self._call_state_kind:
            self._call_state_kind = kind
            self._style_call_card()

    def _set_rec_idle(self, recording: bool) -> None:
        if self._processing:
            return
        t = theme.active()
        if recording:
            self._rec_btn.setText("Parar e processar")
            self._rec_btn.setIcon(theme.qicon("stop", color=t.on_accent))
        else:
            self._rec_btn.setText("Gravar agora")
            self._rec_btn.setIcon(theme.qicon("record", color=t.on_accent))

    # ---- dados da capa: recentes, stats, pendências ---------------------------

    def refresh_home(self) -> None:
        """Recarrega recentes/stats/pendências do índice, em thread."""
        threading.Thread(target=self._collect_home, daemon=True, name="home").start()

    def _collect_home(self) -> None:
        """Lê o índice de reuniões e as pendências (fora da GUI) e marshala o
        render. Só roda com um app real (tem `cfg`): no harness/guard sem app,
        a capa fica no estado inicial e não toca o índice."""
        if getattr(self.app, "cfg", None) is None:
            return
        from .. import meetings_index, notes

        data = {"total": 0, "recent": [], "seconds": 0.0, "clients": 0,
                "pending": [], "pending_stale": 0, "pending_days": 0}
        try:
            data["total"] = meetings_index.count()
        except Exception as e:
            log.debug("count() falhou: %s", e)
        try:
            done = meetings_index.search(status="done", limit=500)
        except Exception as e:
            log.debug("search() falhou: %s", e)
            done = []
        data["recent"] = done[:_RECENT_N]
        data["seconds"] = sum((m.get("duration_s") or 0) for m in done)
        data["clients"] = len({(m.get("client") or "").strip()
                               for m in done if (m.get("client") or "").strip()})
        # Recorte temporal (#79): só pendências de reuniões dos últimos N dias contam como
        # ATIVAS na capa; o resto é backlog ("M antigas"). Idade pela DATA DA REUNIÃO
        # (started_at). days == 0 = sem recorte (conta tudo, como antes).
        days = 0
        try:
            days = int(self.app.cfg.ui.pending_window_days)
        except Exception as e:
            log.debug("pending_window_days indisponível: %s", e)
        data["pending_days"] = days
        try:
            if days > 0:
                from datetime import datetime, timedelta
                cutoff = (datetime.now() - timedelta(days=days)).isoformat()
                recent = [m for m in done if _started_within(m, cutoff)]
                data["pending"] = notes.open_action_items(recent)
                # backlog completo vem do índice (não da lista truncada em 500); guarda
                # contra índice defasado com max(0, ...).
                try:
                    total_open = meetings_index.action_counts().get("open", 0)
                except Exception as e:
                    log.debug("action_counts falhou: %s", e)
                    total_open = len(data["pending"])
                data["pending_stale"] = max(0, total_open - len(data["pending"]))
            else:
                data["pending"] = notes.open_action_items(done)
        except Exception as e:
            log.debug("open_action_items falhou: %s", e)
        self.app.ui(lambda: self._render_home(data))

    def _render_home(self, data: dict) -> None:
        self._home_data = data   # cache p/ re-render na troca de tema (restyle_theme)
        t = theme.active()
        # -- estatísticas
        self._stat_meetings._val.setText(f"<b>{data['total']}</b> reuniões")
        self._stat_hours._val.setText(f"<b>{_fmt_hours(data['seconds'])}</b> gravadas")
        self._stat_clients._val.setText(f"<b>{data['clients']}</b> clientes")
        # -- reuniões recentes (ou empty-state)
        _clear_layout(self._recent_lay)
        recent = data["recent"]
        if not recent:
            self._render_empty_state(data["total"])
        else:
            for m in recent:
                self._recent_lay.addWidget(self._recent_item(m))
        # -- pendências (só as ATIVAS contam no badge; o backlog vira "M antigas", #79)
        _clear_layout(self._pending_lay)
        pend = data["pending"]
        n = len(pend)
        stale = data.get("pending_stale", 0)
        days = data.get("pending_days", 0)
        self._pending_badge.setText(str(n))
        self._pending_badge.setVisible(n > 0)
        self._pending_badge.setToolTip(
            (f"{n} ativa(s) — reuniões dos últimos {days} dias" if days > 0
             else f"{n} pendência(s) — sem recorte") if n else "")
        self._pending_open.setVisible(n > 0)
        self._pending_active_n = n   # base p/ o badge decrementar ao marcar/dispensar (#80)
        if not pend:
            empty = QLabel("Nada pendente por aqui. ✓")
            empty.setProperty("role", "muted")
            empty.setStyleSheet(f"color:{t.muted}; font-size:{t.font_size_small}pt;")
            self._pending_lay.addWidget(empty)
        else:
            shown = pend[:_PENDING_N]
            for header_it, items in _group_by_note(shown):
                self._pending_lay.addWidget(self._pending_group(header_it, items))
            if n > len(shown):
                more = QLabel(f'<a href="#">+{n - len(shown)} outras — ver todas</a>')
                more.setProperty("role", "muted")
                more.setStyleSheet(f"color:{t.muted}; font-size:{t.font_size_small}pt;")
                more.linkActivated.connect(lambda _=None: self._open_pending_hub())
                self._pending_lay.addWidget(more)
        # Badge secundário muted: backlog fora do recorte, linka p/ o hub. Só ocupa
        # espaço quando há backlog (sem layout shift).
        if stale > 0:
            old = QLabel(f'<a href="#">{stale} antiga(s) — ver todas</a>')
            old.setProperty("role", "muted")
            old.setStyleSheet(f"color:{t.muted}; font-size:{t.font_size_small}pt;")
            old.setToolTip("Pendências de reuniões fora do recorte. Abrir o hub para revisar/arquivar.")
            old.linkActivated.connect(lambda _=None: self._open_pending_hub())
            self._pending_lay.addWidget(old)

    def _recent_item(self, m: dict) -> QWidget:
        t = theme.active()
        note_path = (m.get("export_path") or "").strip()
        row = _ClickRow(lambda p=note_path: self._open_recent(p))
        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 6, 8, 6)
        rl.setSpacing(9)
        ico = QLabel()
        ico.setPixmap(theme.qicon("folder", color=t.muted).pixmap(15, 15))
        rl.addWidget(ico, 0, Qt.AlignTop)
        mid = QVBoxLayout()
        mid.setSpacing(1)
        title = m.get("meeting_title") or m.get("title") or "(sem título)"
        tl = QLabel(_elide(title, 38))
        tl.setStyleSheet("font-weight:bold;")
        tl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # encolhe, não empurra o chip
        meta = self._recent_meta(m)
        ml = QLabel(meta)
        ml.setProperty("role", "muted")
        ml.setStyleSheet(f"color:{t.muted}; font-size:{t.font_size_small}pt;")
        ml.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        mid.addWidget(tl)
        mid.addWidget(ml)
        rl.addLayout(mid, 1)
        client = (m.get("client") or "").strip()
        if client:
            chip = QLabel(_elide(client, 18))
            chip.setStyleSheet(
                f"background:{t.field}; color:{t.muted}; border-radius:9px;"
                f" padding:1px 8px; font-size:{t.font_size_small}pt;")
            rl.addWidget(chip, 0, Qt.AlignVCenter)
        return row

    def _recent_meta(self, m: dict) -> str:
        parts = []
        started = (m.get("started_at") or "").strip()
        if started:
            parts.append(started[:16].replace("T", " "))
        dur = m.get("duration_s")
        if dur:
            parts.append(_fmt_hours(dur))
        n = len(m.get("participants") or [])
        if n:
            parts.append(f"{n} part.")
        return "  ·  ".join(parts)

    def _pending_group(self, header_it: dict, items: list) -> QWidget:
        """Um grupo de pendências da MESMA reunião: 1 cabeçalho compacto (título · data ·
        cliente, clicável) + N itens de 1 linha. Elimina o subtítulo repetido (#80)."""
        t = theme.active()
        box = QWidget()
        col = QVBoxLayout(box); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(1)
        note_path = (header_it.get("note_path") or "").strip()
        bits = [b for b in (header_it.get("title"), _short_date(header_it.get("started_at")),
                            header_it.get("client")) if b]
        hdr = QLabel(_elide("  ·  ".join(bits), 52))
        hdr.setStyleSheet(f"color:{t.accent_hover}; font-weight:bold; font-size:{t.font_size_small}pt;")
        hdr.setCursor(Qt.PointingHandCursor)
        hdr.mousePressEvent = lambda _e, p=note_path: self._open_pending_hub(p)
        col.addWidget(hdr)
        for it in items:
            col.addWidget(self._pending_item(it))
        return box

    def _label_chip(self, label: str):
        """Chip colorido da 1ª palavra do rótulo (BLOQUEANTE=rec, ABERTO=warn, resto=neutro).
        None se não houver rótulo. A IA não usa vocabulário fechado — labels fora dos dois
        conhecidos caem no neutro sem quebrar."""
        label = (label or "").strip()
        if not label:
            return None
        t = theme.active()
        up = label.upper()
        if "BLOQUEANTE" in up or "BLOCK" in up:
            bg, fg = t.rec, t.on_accent
        elif "ABERTO" in up or "OPEN" in up:
            bg, fg = t.warn, t.on_highlight
        else:
            bg, fg = t.field, t.muted
        chip = QLabel(label.split()[0].upper()); chip.setObjectName("pendChip")
        chip.setStyleSheet(f"QLabel#pendChip {{ background:{bg}; color:{fg};"
                           f" border-radius:9px; padding:1px 7px; font-size:{t.font_size_small}pt; }}")
        return chip

    def _pending_item(self, it: dict) -> QWidget:
        """Item vivo de 1 linha: checkbox REAL (resolve) + chip de rótulo + texto clicável
        (navega) + ✕ dispensar. Áreas de clique disjuntas (checkbox marca, texto navega)."""
        t = theme.active()
        folder = (it.get("folder") or "").strip()
        key = it.get("key") or ""
        note_path = (it.get("note_path") or "").strip()
        row = QWidget()
        rl = QHBoxLayout(row); rl.setContentsMargins(8, 2, 6, 2); rl.setSpacing(7)
        cb = widgets.AnimatedCheckBox()
        cb.setFixedWidth(cb._BOX + 6)   # indicador só: clique disjunto do texto
        rl.addWidget(cb, 0, Qt.AlignVCenter)
        chip = self._label_chip(it.get("label") or "")
        if chip is not None:
            rl.addWidget(chip, 0, Qt.AlignVCenter)
        txt = QLabel(_elide(it.get("text") or it.get("raw") or "", 40))
        txt.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        txt.setCursor(Qt.PointingHandCursor)
        txt.mousePressEvent = lambda _e, p=note_path: self._open_pending_hub(p)
        widgets.add_tooltip(txt, it.get("raw") or it.get("text") or "")
        rl.addWidget(txt, 1)
        dismiss = widgets.icon_button("dismiss", "Dispensar (falso-positivo)",
                                      size=13, color=t.faint)
        rl.addWidget(dismiss, 0, Qt.AlignVCenter)

        def _resolve(on: bool) -> None:
            from .. import notes
            if folder and key:
                notes.set_action_done(folder, key, on)
            self._style_resolved(txt, on)
            self._bump_pending_badge(-1 if on else 1)

        def _dismiss() -> None:
            from .. import notes
            if folder and key:
                notes.set_action_state(folder, key, "dismissed")
            self._style_resolved(txt, True)
            cb.setEnabled(False); dismiss.setEnabled(False)
            self._bump_pending_badge(-1)

        cb.toggled.connect(_resolve)
        dismiss.clicked.connect(_dismiss)
        return row

    def _style_resolved(self, txt_label, on: bool) -> None:
        t = theme.active()
        txt_label.setStyleSheet(f"color:{t.muted}; text-decoration:line-through;" if on else "")

    def _bump_pending_badge(self, delta: int) -> None:
        """Ajusta o badge de ativas sem re-render da home inteira (evita flicker, #80)."""
        n = max(0, getattr(self, "_pending_active_n", 0) + delta)
        self._pending_active_n = n
        self._pending_badge.setText(str(n))
        self._pending_badge.setVisible(n > 0)

    def _open_recent(self, note_path: str) -> None:
        """Clique numa reunião/pendência: abre a nota selecionada (ou só a tela
        de Notas, se a reunião não tem .md exportado)."""
        if note_path:
            self.app.show_notes(note_path)
        else:
            self.app.show_notes()

    def _open_pending_hub(self, note_path: str = None) -> None:
        """Abre o hub de pendências (#82). Tolerante enquanto o hub não existe: cai
        na tela de Notas. #82 troca o alvo para o hub de primeira classe."""
        hub = getattr(self.app, "show_action_hub", None)
        if callable(hub):
            hub(note_path)
        else:
            self.app.show_notes(note_path) if note_path else self.app.show_notes()

    def _render_empty_state(self, total: int) -> None:
        t = theme.active()
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(6, 14, 6, 14)
        bl.setSpacing(6)
        icon = QLabel()
        icon.setPixmap(theme.qicon("sparkle", color=t.accent_hover).pixmap(26, 26))
        icon.setAlignment(Qt.AlignHCenter)
        head = QLabel("Nenhuma reunião ainda" if total == 0 else "Sem reuniões concluídas")
        head.setAlignment(Qt.AlignHCenter)
        head.setStyleSheet("font-weight:bold;")
        sub = QLabel("Grave sua primeira reunião com o botão acima —\nela vira uma nota pesquisável aqui.")
        sub.setAlignment(Qt.AlignHCenter)
        sub.setProperty("role", "muted")
        sub.setStyleSheet(f"color:{t.muted}; font-size:{t.font_size_small}pt;")
        bl.addWidget(icon)
        bl.addWidget(head)
        bl.addWidget(sub)
        self._recent_lay.addWidget(box)

    def _set_recent_placeholder(self, text: str) -> None:
        _clear_layout(self._recent_lay)
        t = theme.active()
        lbl = QLabel(text)
        lbl.setProperty("role", "muted")
        lbl.setStyleSheet(f"color:{t.muted}; font-size:{t.font_size_small}pt; padding:8px 2px;")
        self._recent_lay.addWidget(lbl)

    # ---- status dos serviços ---------------------------------------------------

    def refresh_status(self) -> None:
        threading.Thread(target=self._collect_status, daemon=True, name="status").start()

    def _render_status(self, items) -> None:
        self._status_items = items   # cache p/ re-render na troca de tema (restyle_theme)
        _clear_layout(self._rows_lay)
        t = theme.active()
        for level, name, detail in items:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 2, 0, 2)
            dot = _dot()
            _paint_dot(dot, getattr(t, _LEVELS.get(level, "muted")))
            rl.addWidget(dot, 0, Qt.AlignTop)
            name_lbl = QLabel(name)
            name_lbl.setFixedWidth(150)
            name_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            rl.addSpacing(8)
            rl.addWidget(name_lbl, 0, Qt.AlignTop)
            det = QLabel(detail)
            det.setProperty("role", "muted")
            det.setStyleSheet(f"font-size:{t.font_size_small}pt;")
            det.setWordWrap(True)
            rl.addWidget(det, 1)
            self._rows_lay.addWidget(row)

    def _collect_status(self):
        """Coleta o status dos serviços (detector/áudio/whisper/autostart). Roda
        em thread e marshala o render via app.ui. Sem app real (harness/guard sem
        `cfg`) não há o que coletar - sai quieto em vez de estourar na thread."""
        cfg = getattr(self.app, "cfg", None)
        if cfg is None:
            return
        items = []
        try:
            from ..detector import app_key_status, patterns_from

            for name, exists in app_key_status(patterns_from(cfg.detection)).items():
                items.append(("ok" if exists else "warn", f"Detecção {name}",
                              "ativa" if exists else f"Abra uma call no {name} uma vez"))
        except Exception as e:
            items.append(("warn", "Detecção", str(e)))
        try:
            audio = util.run_audio_probe()
            if audio:
                items.append(("ok", "Microfone", audio.get("mic", "?")))
                items.append(("ok", "Áudio do sistema", audio.get("loopback", "?")))
            else:
                items.append(("warn", "Áudio", "dispositivos indisponíveis — clique em atualizar"))
        except Exception as e:
            items.append(("warn", "Áudio", f"indisponível: {e}"))
        try:
            w = cfg.whisper
            items.append(("ok", "Transcrição (Whisper)", f"{w.model}"))
        except Exception as e:
            items.append(("warn", "Transcrição", f"indisponível: {e}"))
        try:
            from .. import autostart

            on = autostart.is_enabled()
            items.append(("ok" if on else "off", "Iniciar com o Windows", "ligado" if on else "desligado"))
        except Exception as e:
            items.append(("warn", "Iniciar com o Windows", f"indisponível: {e}"))
        if not items:
            items.append(("warn", "Serviços", "não consegui montar a lista — veja o log"))
        self.app.ui(lambda: self._render_status(items))

    # ---- público ---------------------------------------------------------------

    def show(self) -> None:  # noqa: A003
        super().show()
        self.raise_()
        self.activateWindow()
        if not self._titlebar_done:
            self._titlebar_done = True
            widgets.enable_dark_titlebar(self)
        self.refresh_status()
        self.refresh_home()

    def hide(self) -> None:  # noqa: A003
        super().hide()

    def closeEvent(self, event) -> None:
        """O X tira da barra mas o app segue na bandeja (paridade com WM_DELETE_WINDOW)."""
        event.ignore()
        self.hide()


def util_icon():
    from PySide6.QtGui import QIcon

    ico = getattr(util, "ICON_ICO", None)
    if ico and ico.exists():
        return QIcon(str(ico))
    return QIcon(str(util.ICON_PNG))


# --------------------------------------------------------------- harness ------

def main() -> int:
    import sys

    from PySide6.QtWidgets import QApplication

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = QApplication(sys.argv)
    theme.apply(app)

    class _FakeApp:
        cfg = None
        call_active = False
        update_news = None

        def is_recording(self): return False
        def current_call_app(self): return None
        def recording_duration(self): return 0.0
        def call_duration(self): return 0.0
        def show_settings(self): log.info("show_settings")
        def show_notes(self, note_path=None): log.info("show_notes %s", note_path)
        def show_log(self): log.info("show_log")
        def start_recording(self, *a): log.info("start_recording %s", a)
        def stop_recording(self, **k): log.info("stop_recording %s", k)
        def ui(self, fn): fn()

    win = MainWindow(_FakeApp())
    win.resize(580, 700)
    win.show()
    # status + dados de exemplo (o real depende do ScribaApp)
    win._render_status([
        ("ok", "Detecção Teams", "ativa"),
        ("warn", "Detecção no navegador", "Meet/Zoom — abra uma call no navegador uma vez"),
        ("ok", "Microfone", "Headset (Realtek)"),
        ("ok", "Transcrição (Whisper)", "large-v3-turbo · GPU"),
    ])
    win._render_home({
        "total": 14, "seconds": 34920, "clients": 4,
        "recent": [
            {"meeting_title": "Daily ABAP - Migração S/4", "client": "Cliente A",
             "started_at": "2026-07-04T09:15:00", "duration_s": 1920,
             "participants": [{}, {}, {}, {}], "export_path": ""},
            {"meeting_title": "Reforma Tributária - SNOTE", "client": "Cliente B",
             "started_at": "2026-07-03T14:00:00", "duration_s": 3900,
             "participants": [{}, {}, {}], "export_path": ""},
        ],
        "pending": [
            {"text": "Aplicar nota 3456789 no QAS", "title": "Reforma Tributária",
             "client": "Cliente B", "note_path": ""},
        ],
    })
    win.show_update("0.7.0")

    def _cleanup():
        win._tick_timer.stop()
    app.aboutToQuit.connect(_cleanup)
    return app.exec()


if __name__ == "__main__":
    import os

    rc = main()
    logging.shutdown()
    os._exit(rc)
