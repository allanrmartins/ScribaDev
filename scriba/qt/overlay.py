"""Pílula flutuante de gravação em PySide6 (fase B / #46). Porta `scriba/overlay.py`.

MUST-HAVE do app: sempre visível durante call/gravação; a bandeja é adição, nunca
substituta. Mantém a MESMA API pública que `scriba/main.py` já chama (callbacks
on_stop/on_discard/on_record/on_speakers/on_split; métodos set_mode/set_elapsed/
set_status/clear_status/set_processing/reset_speakers/show/hide/destroy), para que o
corte (#53) seja um drop-in. Nada em produção importa isto até lá.

Ganhos sobre a versão tkinter: cantos arredondados de verdade (WA_TranslucentBackground,
sem cor-chave), cores/fonte do tema ativo (troca a quente), DPI-aware nativo.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from PySide6.QtCore import QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from .. import util
from . import theme, widgets

log = logging.getLogger("scriba.qt.overlay")

_W, _H, _R = 304, 44, 21          # dimensões da pílula (idênticas ao tkinter)
_CY = _H // 2
_EDGE_GAP_Y = 8                    # margem da base da tela na posição-padrão
_POS_KEY = "overlay_pos"           # px LÓGICOS do Qt (decisão do #45); a pílula é dona

# Regiões clicáveis (retângulo generoso atrás de cada glifo — trackpad-friendly).
_R_SPLIT = QRect(196, 0, 24, _H)
_R_STOP = QRect(228, 0, 26, _H)
_R_DISCARD = QRect(262, 0, 26, _H)
_R_SPK_MINUS = QRect(141, 0, 18, _H)
_R_SPK_COUNT = QRect(159, 0, 18, _H)
_R_SPK_PLUS = QRect(177, 0, 18, _H)
_R_REC = QRect(8, 0, 150, _H)       # modo idle: todo o rótulo "Gravar reunião"


# -- posição (px lógicos; clamp à área visível) -------------------------------

def _pos_in_bounds(x: int, y: int, gx: int, gy: int, gw: int, gh: int) -> bool:
    """A pílula (_W×_H) em (x,y) cabe inteira dentro do retângulo (gx,gy,gw,gh)?"""
    return gx <= x <= gx + gw - _W and gy <= y <= gy + gh - _H


def _pos_visible(x: int, y: int) -> bool:
    """(x,y) deixa a pílula inteira dentro de ALGUM monitor? (coords lógicas do Qt)."""
    for screen in QApplication.screens():
        g = screen.geometry()
        if _pos_in_bounds(x, y, g.x(), g.y(), g.width(), g.height()):
            return True
    return False


def _default_pos() -> tuple[int, int]:
    """Centro-inferior do monitor primário (#23), em coordenadas lógicas."""
    g = QApplication.primaryScreen().availableGeometry()
    return g.x() + (g.width() - _W) // 2, g.y() + g.height() - _H - _EDGE_GAP_Y


def _load_pos() -> tuple[int, int] | None:
    try:
        pos = util.read_state().get(_POS_KEY)
        return int(pos[0]), int(pos[1])
    except (TypeError, ValueError, IndexError):
        return None


def _save_pos(x: int, y: int) -> None:
    try:
        util.update_state(**{_POS_KEY: [x, y]})
    except Exception:
        pass


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60:02d}:{s % 60:02d}"


class RecordingPill(QWidget):
    """Janela-pílula da call.

    Modos: "recording" (● pulsante, cronômetro, ✂ nova call, ■ encerrar, × descartar,
    stepper − N + de participantes), "idle" (⏺ Gravar reunião), "processing" (● âmbar +
    estágio do pipeline). `set_status`/`clear_status` sobrepõem um texto fixo (ex.:
    "finalizando…") no modo gravação, escondendo os botões.
    """

    def __init__(
        self,
        parent=None,
        *,
        on_stop: Callable[[], None],
        on_discard: Callable[[], None],
        on_record: Callable[[], None] | None = None,
        on_speakers: Callable[[int], None] | None = None,
        on_split: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self.on_stop = on_stop
        self.on_discard = on_discard
        self.on_record = on_record
        self.on_speakers = on_speakers
        self.on_split = on_split

        self._mode = "recording"
        self._status: str | None = None   # texto fixo sobreposto (finalizando…/estágio)
        self._elapsed = "00:00"
        self._pulse_on = True
        self._spk = 1
        self._spk_committed = False
        self._shown = False
        self._drag_off = None
        self._pressed: str | None = None
        self._hover: str | None = None
        self._tip: QWidget | None = None
        self._svg_cache: dict = {}   # (nome, cor) -> QSvgRenderer, p/ os ícones dos controles (#67)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(0.96)
        self.setFixedSize(_W, _H)
        self.setMouseTracking(True)
        self.setCursor(Qt.SizeAllCursor)

        self._pulse = QTimer(self)
        self._pulse.setInterval(600)
        self._pulse.timeout.connect(self._tick_pulse)
        self._pulse.start()

        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.setInterval(700)
        self._tip_timer.timeout.connect(self._tip_show)

    # -- pintura -------------------------------------------------------------

    def paintEvent(self, _e) -> None:
        t = theme.active()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor(t.border))
        p.setBrush(QColor(t.surface))
        p.drawRoundedRect(1, 1, _W - 2, _H - 2, _R, _R)

        if self._mode == "idle":
            self._paint_idle(p, t)
        elif self._mode == "processing":
            self._paint_dot(p, QColor(t.warn))
            self._paint_text(p, t, self._status or "")
        else:  # recording
            self._paint_dot(p, QColor(t.rec if self._pulse_on else t.rec_dim))
            if self._status:  # texto fixo (finalizando…): sem botões
                self._paint_text(p, t, self._status)
            else:
                self._paint_text(p, t, self._elapsed)
                self._paint_recording_controls(p, t)

    def _paint_dot(self, p: QPainter, color: QColor) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(16, _CY - 6, 12, 12)

    def _paint_text(self, p: QPainter, t, text: str) -> None:
        p.setPen(QColor(t.text))
        p.setFont(theme.qfont(t, 12, bold=True))
        p.drawText(44, 0, 150, _H, Qt.AlignVCenter | Qt.AlignLeft, text)

    def _paint_recording_controls(self, p: QPainter, t) -> None:
        # stepper de participantes: −  N  +
        p.setFont(theme.qfont(t, 13, bold=True))
        p.setPen(QColor(t.text if self._hover == "spk_minus" else t.muted))
        p.drawText(_R_SPK_MINUS, Qt.AlignCenter, "−")
        p.setPen(QColor(t.text if self._hover == "spk_plus" else t.muted))
        p.drawText(_R_SPK_PLUS, Qt.AlignCenter, "+")
        p.setFont(theme.qfont(t, 11, bold=True))
        p.setPen(QColor(t.text if self._spk_committed else t.muted))
        p.drawText(_R_SPK_COUNT, Qt.AlignCenter, str(self._spk))
        # ícones Fluent (#67): nova call (cut) · encerrar (stop) · descartar (dismiss),
        # renderizados via QSvgRenderer nas mesmas regiões clicáveis, cor por hover.
        self._draw_icon(p, "cut", t.text if self._hover == "split" else t.muted, _R_SPLIT, 17)
        self._draw_icon(p, "stop", t.rec if self._hover == "stop" else t.text, _R_STOP, 16)
        self._draw_icon(p, "dismiss", t.text if self._hover == "discard" else t.muted, _R_DISCARD, 16)

    def _svg(self, name: str, color: str):
        """QSvgRenderer (cacheado por nome+cor) do ícone Fluent recolorido, ou None."""
        key = (name, color)
        r = self._svg_cache.get(key)
        if r is None:
            from PySide6.QtSvg import QSvgRenderer

            path = theme.icon(name, color)
            if not path:
                return None
            r = QSvgRenderer(path)
            self._svg_cache[key] = r
        return r

    def _draw_icon(self, p: QPainter, name: str, color: str, region: QRect, size: int) -> None:
        r = self._svg(name, color)
        if r is None:
            return
        cx, cy = region.center().x(), region.center().y()
        r.render(p, QRectF(cx - size / 2, cy - size / 2, size, size))

    def _paint_idle(self, p: QPainter, t) -> None:
        rec = QColor(t.rec)
        pen = p.pen()
        pen.setColor(rec)
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(15, _CY - 8, 16, 16)          # anel
        p.setPen(Qt.NoPen)
        p.setBrush(rec)
        p.drawEllipse(19, _CY - 4, 8, 8)            # ponto
        p.setPen(QColor(t.rec if self._hover == "rec" else t.text))
        p.setFont(theme.qfont(t, 10, bold=True))
        p.drawText(44, 0, 200, _H, Qt.AlignVCenter | Qt.AlignLeft, "Gravar reunião")

    # -- regiões clicáveis por estado ----------------------------------------

    def _regions(self) -> dict[str, QRect]:
        if self._mode == "idle":
            return {"rec": _R_REC}
        if self._mode == "recording" and not self._status:
            return {"split": _R_SPLIT, "stop": _R_STOP, "discard": _R_DISCARD,
                    "spk_minus": _R_SPK_MINUS, "spk_count": _R_SPK_COUNT, "spk_plus": _R_SPK_PLUS}
        return {}  # processing / status fixo: sem botões

    def _button_at(self, pos) -> str | None:
        for name, rect in self._regions().items():
            if rect.contains(pos):
                return name
        return None

    # -- clique + arraste ----------------------------------------------------

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            return
        self._tip_hide()
        self._pressed = self._button_at(e.position().toPoint())
        self._drag_off = None if self._pressed else e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_off is not None:
            self.move(e.globalPosition().toPoint() - self._drag_off)
            return
        self._update_hover(e.position().toPoint())

    def mouseReleaseEvent(self, e) -> None:
        if self._drag_off is not None:
            self._drag_off = None
            _save_pos(self.x(), self.y())
            return
        if self._pressed and self._pressed == self._button_at(e.position().toPoint()):
            self._fire(self._pressed)
        self._pressed = None

    def _fire(self, region: str) -> None:
        if region == "stop":
            self.on_stop()
        elif region == "discard":
            self.on_discard()
        elif region == "split" and self.on_split:
            self.on_split()
        elif region == "rec" and self.on_record:
            self.on_record()
        elif region == "spk_minus":
            self._spk_adjust(-1)
        elif region == "spk_plus":
            self._spk_adjust(+1)
        elif region == "spk_count":
            self._commit_speakers()  # confirma o valor à mostra

    def _update_hover(self, pos) -> None:
        btn = self._button_at(pos)
        self.setCursor(Qt.PointingHandCursor if btn else Qt.SizeAllCursor)
        if btn != self._hover:
            self._hover = btn
            self.update()

    def enterEvent(self, _e) -> None:
        self._tip_timer.start()

    def leaveEvent(self, _e) -> None:
        self._tip_timer.stop()
        self._tip_hide()
        if self._hover is not None:
            self._hover = None
            self.update()

    # -- participantes (#13) -------------------------------------------------

    def reset_speakers(self, suggested: int) -> None:
        """Nova gravação: sugere o último valor (cinza) até o usuário ajustar/confirmar."""
        self._spk = max(1, int(suggested) if suggested else 1)
        self._spk_committed = False
        self.update()

    def _spk_adjust(self, delta: int) -> None:
        self._spk = max(1, min(20, self._spk + delta))
        self._commit_speakers()

    def _commit_speakers(self) -> None:
        self._spk_committed = True
        self.update()
        if self.on_speakers:
            self.on_speakers(self._spk)

    # -- estado / modos ------------------------------------------------------

    def _tick_pulse(self) -> None:
        if self._mode == "recording" and not self._status:
            self._pulse_on = not self._pulse_on
            self.update()
        if self._shown:
            self._ensure_visible()

    def set_mode(self, mode: str) -> None:
        """Alterna "recording" (●/cronômetro/■/×) e "idle" (⏺ Gravar reunião)."""
        self._mode = mode
        self._status = None
        self.update()

    def set_elapsed(self, seconds: float) -> None:
        if self._status is not None:
            return
        self._elapsed = _fmt_elapsed(seconds)
        self.update()

    def set_status(self, text: str) -> None:
        """Texto fixo sem botões no modo gravação (ex.: 'finalizando…')."""
        self._status = text
        self.update()

    def set_processing(self, text: str) -> None:
        """Pós-call fora de call: ● âmbar + estágio ('Transcrevendo…'), sem botões."""
        self._mode = "processing"
        self._status = text
        self.update()

    def clear_status(self) -> None:
        self._status = None
        self.update()

    # -- visibilidade / posição ----------------------------------------------

    def _ensure_visible(self) -> None:
        """Mantém no topo e visível durante a call (um fullscreen/compartilhamento pode
        roubar o topmost). NÃO mexe na captura (segue oculta no compartilhamento)."""
        try:
            self.raise_()
            if not self.isVisible():
                self.show()
        except RuntimeError:
            pass

    def show(self) -> None:  # noqa: A003 (mantém o nome da API pública)
        pos = _load_pos()
        if pos and _pos_visible(*pos):
            x, y = pos
        else:
            x, y = _default_pos()
        self.move(x, y)
        self._shown = True
        super().show()
        self.raise_()
        widgets.exclude_from_capture(self)  # reaplica (defensivo)
        self._ensure_visible()

    def hide(self) -> None:  # noqa: A003
        self._shown = False
        self._tip_hide()
        super().hide()

    def destroy(self) -> None:  # noqa: A003
        self._shown = False
        self._pulse.stop()
        self._tip_hide()
        self.close()
        self.deleteLater()

    # -- tooltip "invisível no compartilhamento" (#23) -----------------------

    _TIP_TEXT = ("Esta janela não aparece para quem assiste ao seu compartilhamento "
                 "de tela  ·  arraste para mover")

    def _tip_show(self) -> None:
        if self._tip is not None or not self._shown:
            return
        from PySide6.QtWidgets import QLabel

        t = theme.active()
        tip = QLabel(self._TIP_TEXT)
        tip.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.ToolTip)
        tip.setWordWrap(True)
        tip.setMaximumWidth(240)
        tip.setStyleSheet(
            f"background:{t.overlay}; color:{t.muted}; border:1px solid {t.border};"
            f" border-radius:{t.radius_sm}px; padding:6px 10px;"
        )
        tip.adjustSize()
        gx, gy = self.x(), self.y()
        tx = gx + (_W - tip.width()) // 2
        ty = gy - tip.height() - 6
        if ty < 0:
            ty = gy + _H + 6
        tip.move(tx, ty)
        tip.show()
        widgets.exclude_from_capture(tip)  # o hint também não vaza (#9)
        self._tip = tip

    def _tip_hide(self) -> None:
        self._tip_timer.stop()
        if self._tip is not None:
            self._tip.close()
            self._tip = None


def run_countdown(seconds: int) -> str:
    """Pílula bloqueante para gravações manuais (`scriba record N`).
    Retorna "ok" (tempo esgotado ou ■) ou "discarded" (×). Porta o equivalente tkinter."""
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    outcome = {"v": "ok"}

    def stop() -> None:
        outcome["v"] = "ok"
        app.quit()

    def discard() -> None:
        outcome["v"] = "discarded"
        app.quit()

    pill = RecordingPill(on_stop=stop, on_discard=discard)
    pill.set_mode("recording")
    pill.show()
    t0 = time.monotonic()

    timer = QTimer()

    def tick() -> None:
        elapsed = time.monotonic() - t0
        if elapsed >= seconds:
            app.quit()
            return
        pill.set_elapsed(elapsed)

    timer.timeout.connect(tick)
    timer.start(250)
    app.exec()
    timer.stop()
    pill.destroy()
    return outcome["v"]


# --------------------------------------------------------------- harness ------

def main() -> int:
    """`python -m scriba.qt.overlay`: pílula com callbacks fake + painel p/ alternar modos."""
    import sys

    from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = QApplication(sys.argv)
    theme.apply(app)

    pill = RecordingPill(
        on_stop=lambda: log.info("STOP (guardar)"),
        on_discard=lambda: log.info("DISCARD"),
        on_record=lambda: (log.info("RECORD"), pill.set_mode("recording"), pill.reset_speakers(2)),
        on_speakers=lambda n: log.info("participantes = %d", n),
        on_split=lambda: log.info("SPLIT (nova call)"),
    )
    pill.set_mode("recording")
    pill.reset_speakers(2)
    pill.show()
    widgets.exclude_from_capture(pill)

    elapsed = {"s": 0.0}

    def tick() -> None:
        elapsed["s"] += 0.5
        pill.set_elapsed(elapsed["s"])

    ticker = QTimer()
    ticker.timeout.connect(tick)
    ticker.start(500)

    # painel de controle: alterna todos os modos/estados (o "atalho" do harness)
    ctrl = QWidget()
    ctrl.setWindowTitle("Pílula Qt — harness (#46)")
    lay = QVBoxLayout(ctrl)
    lay.addWidget(QLabel("Alternar modos/estados da pílula:"))
    row1 = QHBoxLayout()
    row1.addWidget(widgets.ModernButton("Gravando", lambda: pill.set_mode("recording"), kind="primary"))
    row1.addWidget(widgets.ModernButton("Idle (Gravar reunião)", lambda: pill.set_mode("idle")))
    lay.addLayout(row1)
    row2 = QHBoxLayout()
    row2.addWidget(widgets.ModernButton("Status: finalizando…", lambda: pill.set_status("finalizando…")))
    row2.addWidget(widgets.ModernButton("Limpar status", pill.clear_status))
    lay.addLayout(row2)
    row3 = QHBoxLayout()
    row3.addWidget(widgets.ModernButton("Processing: transcrevendo", lambda: pill.set_processing("Transcrevendo…")))
    row3.addWidget(widgets.ModernButton("Reset participantes (2)", lambda: pill.reset_speakers(2)))
    lay.addLayout(row3)
    widgets.remember_geometry(ctrl, "harness_overlay_ctrl", default=(80, 80, 460, 170))
    ctrl.show()
    widgets.enable_dark_titlebar(ctrl)

    def _cleanup() -> None:
        ticker.stop()
    app.aboutToQuit.connect(_cleanup)

    log.info("harness da pílula rodando. Use o painel para alternar modos; arraste a pílula.")
    return app.exec()


if __name__ == "__main__":
    import os

    rc = main()
    logging.shutdown()
    os._exit(rc)
