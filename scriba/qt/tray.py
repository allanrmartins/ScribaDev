"""Bandeja do ScribaDev em PySide6 (fase B / #52). Porta `scriba/tray.py` (pystray).

Mesma API pública que `scriba/main.py` usa: `Tray(app)`, `start()`, `stop()`,
`set_recording(recording, title, dim=)`. A bandeja é o indicador CONFIÁVEL de
gravação (a pílula pode ser ocultada); o pulso REC (#14) continua — `main.py` chama
`set_recording(..., dim=alterna)` num tick, e aqui isso troca o ícone esmaecido.

Diferença de idioma vs pystray: o QMenu não tem visibilidade por-item via callback;
sincronizamos tudo (linha de status, ações só-em-gravação, radios de participantes,
autostart) em `aboutToShow`. Ações longas do app seguem indo pra thread própria
(`_bg`), como no pystray; só o diálogo de descarte roda direto (a bandeja Qt já
está na thread da GUI). Integração real com o ScribaApp é fase C.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Qt
from PySide6.QtGui import QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon

from .. import autostart, util
from . import theme

log = logging.getLogger("scriba.qt.tray")


def _icon(recording: bool, dim: bool = False) -> QIcon:
    """Ícone do app (pergaminho + raio) via asset; cai p/ um círculo desenhado se sumir.
    `dim` (só com recording): variante APAGADA — alternada com a normal faz a bandeja
    PULSAR durante a gravação (#14)."""
    png = util.ICON_REC_PNG if recording else util.ICON_PNG
    pm = QPixmap(str(png))
    if pm.isNull():
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor("#e54545") if recording else QColor("#f0f0f5"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(14, 14, 36, 36)
        p.end()
    if recording and dim:
        faded = QPixmap(pm.size())
        faded.fill(Qt.transparent)
        p = QPainter(faded)
        p.setOpacity(0.4)
        p.drawPixmap(0, 0, pm)
        p.end()
        pm = faded
    return QIcon(pm)


class Tray:
    def __init__(self, app):
        self.app = app
        self._tray = QSystemTrayIcon(_icon(False))
        self._tray.setToolTip("ScribaDev — aguardando call do Teams")

        m = QMenu()
        self._menu = m
        self._status = m.addAction("")           # linha de status (desabilitada)
        self._status.setEnabled(False)
        m.addSeparator()
        m.addAction("Abrir ScribaDev", lambda: self.app.show_main())  # = duplo clique
        m.addAction("Notas", lambda: self.app.show_notes())
        m.addAction("Pendências", lambda: self.app.show_action_hub())
        m.addAction("Log", lambda: self.app.show_log())
        m.addAction("Configurações", lambda: self.app.show_settings())
        self._theme_menu = self._build_theme_menu()
        m.addMenu(self._theme_menu)

        # ações só-em-gravação (visibilidade alternada em _sync)
        self._act_record = m.addAction("Gravar agora", lambda: self._bg(self.app.start_recording, "manual"))
        self._act_split = m.addAction("Nova call (dividir gravação)", lambda: self._bg(self.app._split_now))
        self._act_stop = m.addAction("Parar gravação", lambda: self._bg(self.app.stop_recording, False, True))
        self._act_discard = m.addAction("Descartar gravação", self._discard)
        self._spk_menu = self._build_speakers_menu()
        self._act_speakers = m.addMenu(self._spk_menu)

        m.addAction("Abrir pasta de reuniões",
                    lambda: util.open_path(self.app.cfg.output.resolved_recordings_dir()))
        m.addAction("Abrir pasta de notas", self._open_notes_dir)
        m.addAction("Processar pendentes", lambda: self._bg(self.app.scan_pending))
        m.addAction("Buscar atualizações", lambda: self._bg(self.app.check_updates_now))
        self._act_autostart = m.addAction("Iniciar com o Windows", self._toggle_autostart)
        self._act_autostart.setCheckable(True)
        m.addSeparator()
        m.addAction("Sair", lambda: self.app.request_quit())

        m.aboutToShow.connect(self._sync)
        self._tray.setContextMenu(m)
        self._tray.activated.connect(self._on_activated)

    # -- submenu de participantes (1-8, radio) -------------------------------

    def _build_speakers_menu(self) -> QMenu:
        menu = QMenu("Participantes")
        self._spk_group = QActionGroup(menu)
        self._spk_group.setExclusive(True)
        self._spk_acts = {}
        for n in range(1, 9):
            act = menu.addAction(str(n))
            act.setCheckable(True)
            act.triggered.connect(lambda _checked, k=n: self._bg(self.app._set_speakers_live, k))
            self._spk_group.addAction(act)
            self._spk_acts[n] = act
        return menu

    # -- submenu de tema (Automático + 4 temas, radio) -----------------------

    def _build_theme_menu(self) -> QMenu:
        menu = QMenu("Tema")
        self._theme_group = QActionGroup(menu)
        self._theme_group.setExclusive(True)
        self._theme_acts = []   # [(QAction, slug|None)]

        def add(label: str, name) -> None:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.triggered.connect(lambda _checked=False, n=name: self._switch_theme(n))
            self._theme_group.addAction(act)
            self._theme_acts.append((act, name))

        add("Automático (segue o Windows)", None)
        for th in theme.themes():
            add(th.label, th.name)
        return menu

    def _switch_theme(self, name) -> None:
        from PySide6.QtWidgets import QApplication

        theme.apply_choice(name, app=QApplication.instance())

    # -- sincronização (estado atual do app) ---------------------------------

    def _sync(self) -> None:
        rec = self.app.is_recording()
        self._status.setText(self.app.status_text())
        self._act_record.setVisible(not rec)
        for act in (self._act_split, self._act_stop, self._act_discard, self._act_speakers):
            act.setVisible(rec)
        if rec:
            cur = self.app.current_speakers()
            for n, act in self._spk_acts.items():
                act.setChecked(cur == n)
        self._act_autostart.setChecked(autostart.is_enabled())
        choice = theme.current_choice()
        for act, name in self._theme_acts:
            act.setChecked(name == choice)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.app.show_main()

    # -- ações ---------------------------------------------------------------

    def _bg(self, fn, *args) -> None:
        """Ações longas do app fora da thread da GUI (paridade com o pystray)."""
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _open_notes_dir(self) -> None:
        d = self.app.cfg.output.resolved_export_dir()
        d.mkdir(parents=True, exist_ok=True)
        util.open_path(d)

    def _discard(self) -> None:
        """DESTRUTIVO: confirma antes de perder o áudio. A bandeja Qt já roda na thread
        da GUI, então o diálogo vai direto (sem o hop de thread do pystray)."""
        ok = QMessageBox.question(
            None, "Descartar gravação?",
            "A gravação em andamento será descartada e o áudio, perdido.\n\nDescartar mesmo assim?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes
        if ok:
            self._bg(self.app.stop_recording, True)

    def _toggle_autostart(self) -> None:
        self._bg(autostart.set_autostart, not autostart.is_enabled())

    # -- API pública (igual à do pystray) ------------------------------------

    def start(self) -> None:
        self._tray.show()

    def stop(self) -> None:
        self._tray.hide()

    def set_recording(self, recording: bool, title: str, dim: bool = False) -> None:
        self._tray.setIcon(_icon(recording, dim=dim))
        self._tray.setToolTip(title)
