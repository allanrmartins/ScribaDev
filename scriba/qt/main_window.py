"""Janela principal (capa) do ScribaDev em PySide6 (fase B / #47). Porta `scriba/main_window.py`.

Rosto do app: status dos serviços, call ao vivo e gravação manual. Comportamento de
janela "de verdade": o X tira da barra mas o app segue na bandeja (closeEvent -> hide).
Mesma API pública que `main.py` chama: `show()`, `hide()`, `show_update(ver)`. A coleta
de status (`_collect_status`) chama detector/util como na versão tk; o render é
separado (`_render_status`) para o harness/testes alimentarem itens prontos. Integração
com o ScribaApp = fase C.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, updates, util
from . import theme, widgets

log = logging.getLogger("scriba.qt.main_window")

_LEVELS = {"ok": "ok", "warn": "warn", "off": "muted"}  # nível -> token de cor


def _fmt(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def _dot(diameter: int = 10) -> QLabel:
    d = QLabel()
    d.setFixedSize(diameter, diameter)
    return d


def _paint_dot(dot: QLabel, hexcolor: str) -> None:
    r = dot.width() // 2
    dot.setStyleSheet(f"background:{hexcolor}; border-radius:{r}px;")


class MainWindow(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._processing = False
        self._update_ver = None
        self._titlebar_done = False

        self.setWindowTitle(f"ScribaDev v{__version__}")
        self.setMinimumSize(520, 560)
        try:
            self.setWindowIcon(util_icon())
        except Exception:
            pass

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(0)

        # ---- cabeçalho -------------------------------------------------------
        head = QHBoxLayout()
        title = QLabel("ScribaDev")
        title.setStyleSheet("font-size:16pt; font-weight:bold;")
        head.addWidget(title)
        ver = QLabel(updates.build_string())
        ver.setProperty("role", "muted")
        ver.setStyleSheet("font-size:9pt;")
        head.addWidget(ver, 0, Qt.AlignBottom)
        head.addStretch(1)
        head.addWidget(widgets.ModernButton("Log", lambda: self.app.show_log()))
        head.addWidget(widgets.ModernButton("Notas", lambda: self.app.show_notes()))
        head.addWidget(widgets.ModernButton("⚙", lambda: self.app.show_settings()))
        root.addLayout(head)

        # ---- aviso de nova versão (#19): oculto até haver atualização ---------
        self._update_bar = QFrame()
        self._update_bar.setObjectName("updateBar")
        ub = QHBoxLayout(self._update_bar)
        ub.setContentsMargins(12, 8, 12, 8)
        self._update_lbl = QLabel("")
        self._update_btn = widgets.ModernButton("Atualizar agora", self._do_update, kind="primary")
        ub.addWidget(self._update_lbl)
        ub.addStretch(1)
        ub.addWidget(self._update_btn)
        self._update_bar.hide()
        root.addWidget(self._update_bar)
        root.addSpacing(8)

        # ---- call ao vivo ----------------------------------------------------
        self._card = QFrame()
        self._card.setObjectName("callCard")
        cv = QVBoxLayout(self._card)
        cv.setContentsMargins(16, 14, 16, 14)
        state_row = QHBoxLayout()
        self._call_dot = _dot()
        self._call_state = QLabel("Nenhuma ligação em andamento")
        self._call_state.setProperty("role", "muted")
        state_row.addWidget(self._call_dot)
        state_row.addSpacing(7)
        state_row.addWidget(self._call_state)
        state_row.addStretch(1)
        cv.addLayout(state_row)
        self._call_timer = QLabel("—")
        self._call_timer.setStyleSheet("font-size:28pt; font-weight:bold;")
        cv.addWidget(self._call_timer)
        self._rec_btn = widgets.ModernButton("⏺  Gravar agora", self._toggle_record, kind="primary")
        cv.addWidget(self._rec_btn, 0, Qt.AlignLeft)
        root.addWidget(self._card)
        root.addSpacing(16)

        # ---- serviços --------------------------------------------------------
        sect = QHBoxLayout()
        slbl = QLabel("Serviços")
        slbl.setStyleSheet("font-weight:bold;")
        sect.addWidget(slbl)
        sect.addStretch(1)
        link = QLabel('<a href="#">atualizar</a>')
        link.setStyleSheet(f"color:{theme.active().muted};")
        link.linkActivated.connect(lambda _=None: self.refresh_status())
        sect.addWidget(link)
        root.addLayout(sect)
        root.addSpacing(4)

        self._rows = QWidget()
        self._rows_lay = QVBoxLayout(self._rows)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(6)
        root.addWidget(self._rows, 1)

        self._apply_theme()
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(1000)

    # ---- tema ------------------------------------------------------------------

    def _apply_theme(self) -> None:
        t = theme.active()
        self.setStyleSheet(
            f"QFrame#callCard, QFrame#updateBar {{ background:{t.surface};"
            f" border-radius:{t.radius + 2}px; }}"
        )

    # ---- gravação --------------------------------------------------------------

    def _toggle_record(self) -> None:
        if self._processing:
            return
        if self.app.is_recording():
            self._processing = True
            self._rec_btn.setText("Processando…")
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

    # ---- aviso de nova versão (#19) -------------------------------------------

    def show_update(self, ver: str) -> None:
        self._update_ver = ver
        self._update_lbl.setText(f"⬆  Nova versão v{ver} disponível")
        self._update_lbl.setStyleSheet(f"color:{theme.active().ok}; font-weight:bold;")
        self._update_btn.setText("Atualizar agora" if updates.is_git_install() else "Baixar")
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
            self._set_rec_idle("■  Parar e processar")
            _paint_dot(self._call_dot, t.accent)
        elif getattr(self.app, "call_active", False):
            app_name = self.app.current_call_app()
            self._call_state.setText(f"Em call ({app_name or '?'}) — sem gravar")
            self._call_timer.setText(_fmt(self.app.call_duration()))
            self._set_rec_idle("⏺  Gravar agora")
            _paint_dot(self._call_dot, t.warn)
        else:
            self._call_state.setText("Nenhuma ligação em andamento")
            self._call_timer.setText("—")
            self._set_rec_idle("⏺  Gravar agora")
            _paint_dot(self._call_dot, t.muted)
        news = getattr(self.app, "update_news", None)
        if news and self._update_bar.isHidden():
            self.show_update(news)

    def _set_rec_idle(self, text: str) -> None:
        if not self._processing:
            self._rec_btn.setText(text)

    # ---- status dos serviços ---------------------------------------------------

    def refresh_status(self) -> None:
        threading.Thread(target=self._collect_status, daemon=True, name="status").start()

    def _render_status(self, items) -> None:
        # limpa as linhas antigas
        while self._rows_lay.count():
            child = self._rows_lay.takeAt(0)
            w = child.widget()
            if w is not None:
                w.deleteLater()
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
            det.setStyleSheet("font-size:8pt;")
            det.setWordWrap(True)
            rl.addWidget(det, 1)
            self._rows_lay.addWidget(row)

    def _collect_status(self):
        """Coleta o status dos serviços (detector/áudio/whisper/resumo/diarização/
        autostart). Idêntico em espírito à versão tk; roda em thread e marshala o
        render via app.ui. Não exercitado no harness/teste (é domínio do app)."""
        items = []
        cfg = self.app.cfg
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
        call_active = False
        update_news = None

        def is_recording(self): return False
        def current_call_app(self): return None
        def recording_duration(self): return 0.0
        def call_duration(self): return 0.0
        def show_settings(self): log.info("show_settings")
        def show_notes(self): log.info("show_notes")
        def show_log(self): log.info("show_log")
        def start_recording(self, *a): log.info("start_recording %s", a)
        def stop_recording(self, **k): log.info("stop_recording %s", k)
        def ui(self, fn): fn()

    win = MainWindow(_FakeApp())
    win.resize(560, 640)
    win.show()
    # status de exemplo (o real depende do ScribaApp)
    win._render_status([
        ("ok", "Detecção Teams", "ativa"),
        ("warn", "Detecção no navegador", "Meet/Zoom — abra uma call no navegador uma vez"),
        ("ok", "Microfone", "Headset (Realtek)"),
        ("ok", "Transcrição (Whisper)", "large-v3-turbo · GPU"),
        ("off", "Resumo", "desativado"),
        ("warn", "Diarização", "dependências ausentes — instale o extra [diarization]"),
    ])
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
