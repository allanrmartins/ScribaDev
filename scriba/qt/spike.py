"""Spike da fase A (#45): de-risca a pílula + o sistema de temas em Qt, na tela REAL.

Roda com `python -m scriba.qt.spike`. Prova, num só executável, os riscos que
justificam o épico #44 antes de investir na reescrita das janelas:

  1. pílula frameless + translúcida + always-on-top + cantos arredondados de verdade
     (WA_TranslucentBackground: sem o hack da cor-chave do tkinter) na posição salva;
  2. costura thread->UI: uma `queue.Queue` drenada por QTimer (UiPump), com uma thread
     de fundo empurrando comandos que mexem na pílula SEM warning de thread do Qt;
  3. bandeja (QSystemTrayIcon) com troca de TEMA ao vivo;
  4. exclusão de captura (WDA_EXCLUDEFROMCAPTURE): a pílula some do compartilhamento;
  5. SISTEMA DE TEMAS: um painel de controles + a pílula reestilizam na hora ao trocar
     de tema pela bandeja (cor, fonte e fundo) — o de-risk do que o Allan pediu.

Nada aqui vira código de produção; as janelas de verdade são a fase B. Este arquivo
existe para o Allan olhar nos 2 monitores e bater o martelo (posição, tema, contraste).
"""

from __future__ import annotations

import logging
import sys
import threading
import time

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import util
from . import theme, widgets
from .pump import UiPump

log = logging.getLogger("scriba.qt.spike")

_W, _H, _R = 304, 44, 21  # mesmas dimensões da pílula tkinter (overlay.py)

_SPIKE_POS_KEY = "overlay_pos_qt_spike"  # separado do overlay_pos do tkinter (não clobbar)

# Regiões clicáveis do ■ (pausar/retomar) e do × (fechar), casando com o paintEvent.
# Retângulo maior que o glifo (~13px é difícil de acertar no trackpad), como as
# hitboxes invisíveis da pílula tkinter (overlay.py).
_STOP_RECT = QRect(_W - 78, 0, 28, _H)
_CLOSE_RECT = QRect(_W - 44, 0, 28, _H)


def _default_pos() -> tuple[int, int]:
    """Centro-inferior do monitor primário, em coordenadas LÓGICAS do Qt."""
    scr = QApplication.primaryScreen().availableGeometry()
    return scr.x() + (scr.width() - _W) // 2, scr.y() + scr.height() - _H - 8


def _pos_visible(x: int, y: int) -> bool:
    """(x,y,_W,_H) cabe inteiro dentro de ALGUM monitor? (coords lógicas)."""
    for screen in QApplication.screens():
        g = screen.geometry()
        if g.x() <= x <= g.x() + g.width() - _W and g.y() <= y <= g.y() + g.height() - _H:
            return True
    return False


def _load_pos() -> tuple[int, int] | None:
    st = util.read_state()
    for key in (_SPIKE_POS_KEY, "overlay_pos"):  # preferir o do spike; cair no legado
        pos = st.get(key)
        try:
            x, y = int(pos[0]), int(pos[1])
        except (TypeError, ValueError, IndexError):
            continue
        return x, y
    return None


class SpikePill(QWidget):
    """Pílula-maquete: valida os riscos (não é a fase B). Lê o tema ativo a cada paint,
    então trocar de tema a reestiliza na hora."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)  # cantos arredondados reais
        self.setWindowOpacity(0.96)
        self.setFixedSize(_W, _H)
        self.setCursor(Qt.SizeAllCursor)
        self.setMouseTracking(True)  # hover sem botão pressionado (cursor/realce)

        self._elapsed = "00:00"
        self._status: str | None = None
        self._pulse_on = True
        self._paused = False
        self._drag_off = None
        self._hover: str | None = None       # "stop" | "close" | None
        self._pressed_btn: str | None = None  # botão sob o press atual

        self._pulse = QTimer(self)
        self._pulse.setInterval(600)
        self._pulse.timeout.connect(self._tick_pulse)
        self._pulse.start()

    # -- pintura -------------------------------------------------------------

    def paintEvent(self, _e) -> None:
        t = theme.active()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor(t.border))
        p.setBrush(QColor(t.surface))
        p.drawRoundedRect(1, 1, _W - 2, _H - 2, _R, _R)

        # ponto: âmbar no modo status (processando), senão vermelho pulsante (gravando)
        dot = QColor(t.warn) if self._status else QColor(t.rec if self._pulse_on else t.rec_dim)
        p.setPen(Qt.NoPen)
        p.setBrush(dot)
        p.drawEllipse(16, _H // 2 - 6, 12, 12)

        # texto: cronômetro OU status (fonte moderna do tema: Inter)
        p.setPen(QColor(t.text))
        p.setFont(theme.qfont(t, 12, bold=True))
        p.drawText(44, 0, 150, _H, Qt.AlignVCenter | Qt.AlignLeft, self._status or self._elapsed)

        # ■ pausar/retomar · × fechar. Realce no hover (paridade com a pílula tkinter).
        p.setFont(theme.qfont(t, 13))
        p.setPen(QColor(t.rec if self._hover == "stop" else t.text))
        p.drawText(_STOP_RECT, Qt.AlignCenter, "▶" if self._paused else "■")
        p.setPen(QColor(t.text if self._hover == "close" else t.muted))
        p.drawText(_CLOSE_RECT, Qt.AlignCenter, "×")

    def _tick_pulse(self) -> None:
        if not self._paused:
            self._pulse_on = not self._pulse_on
        # re-assere o topmost (um app fullscreen/compartilhando pode roubá-lo) SEM
        # ativar o app — no macOS o raise_() do Qt rouba o foco (ver
        # widgets.raise_without_activation)
        widgets.raise_without_activation(self)
        self.update()

    # -- clique + arraste coexistindo (o de-risk real p/ a fase B) -----------

    def _button_at(self, pos) -> str | None:
        if _STOP_RECT.contains(pos):
            return "stop"
        if _CLOSE_RECT.contains(pos):
            return "close"
        return None

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            return
        # press num botão NÃO arrasta; press no corpo inicia o arraste.
        self._pressed_btn = self._button_at(e.position().toPoint())
        self._drag_off = None if self._pressed_btn else e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_off is not None:
            self.move(e.globalPosition().toPoint() - self._drag_off)
            return
        self._update_hover(e.position().toPoint())  # cursor/realce quando não arrasta

    def mouseReleaseEvent(self, e) -> None:
        if self._drag_off is not None:
            self._drag_off = None
            util.update_state(**{_SPIKE_POS_KEY: [self.x(), self.y()]})
            log.info("posição salva (lógica): [%d, %d]", self.x(), self.y())
            return
        # clique de verdade: só dispara se soltou DENTRO do mesmo botão do press
        if self._pressed_btn and self._pressed_btn == self._button_at(e.position().toPoint()):
            (self._on_stop if self._pressed_btn == "stop" else self._on_close)()
        self._pressed_btn = None

    def _update_hover(self, pos) -> None:
        btn = self._button_at(pos)
        self.setCursor(Qt.PointingHandCursor if btn else Qt.SizeAllCursor)
        if btn != self._hover:
            self._hover = btn
            self.update()

    def leaveEvent(self, _e) -> None:
        if self._hover is not None:
            self._hover = None
            self.update()

    def _on_stop(self) -> None:
        self._paused = not self._paused
        log.info("■ %s", "pausado" if self._paused else "retomado")
        self.update()

    def _on_close(self) -> None:
        log.info("× fechar")
        QApplication.quit()

    # -- comandos vindos da thread de fundo (via UiPump) ---------------------

    def set_elapsed(self, text: str) -> None:
        if self._paused:  # ■ congela o display; a thread de fundo segue mandando
            return
        self._status = None
        self._elapsed = text
        self.update()

    def set_status(self, text: str) -> None:
        if self._paused:
            return
        self._status = text
        self.update()

    def place(self) -> None:
        pos = _load_pos()
        if pos and _pos_visible(*pos):
            x, y = pos
            log.info("posição salva usada: [%d, %d]", x, y)
        else:
            x, y = _default_pos()
            if pos:
                log.info("posição salva [%d, %d] fora de tela -> padrão [%d, %d]", *pos, x, y)
            else:
                log.info("sem posição salva -> padrão [%d, %d]", x, y)
        self.move(x, y)


class SpikePanel(QWidget):
    """Painel de amostra: controles reais (botões, toggle, stepper, campo de busca,
    rótulos e um chat estilo Claude Code) para VER o tema trocar cor/fonte/fundo ao
    vivo — incluindo o fundo translúcido com gradiente (não terminal, não chapado)."""

    def __init__(self):
        super().__init__()
        self.setObjectName("spikePanel")
        self.setWindowTitle("ScribaDev — demo de tema (spike)")
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        title = QLabel("Amostra de controles")
        title.setStyleSheet("font-size: 12pt; font-weight: bold;")
        lay.addWidget(title)

        row = QHBoxLayout()
        row.addWidget(widgets.ModernButton("Salvar", kind="primary"))
        row.addWidget(widgets.ModernButton("Cancelar"))
        row.addStretch(1)
        lay.addLayout(row)

        toggle_row = QHBoxLayout()
        toggle_row.addWidget(widgets.ToggleSwitch(checked=True))
        toggle_row.addWidget(QLabel("Diarização (separar vozes)"))
        toggle_row.addStretch(1)
        lay.addLayout(toggle_row)

        step_row = QHBoxLayout()
        step_row.addWidget(widgets.Stepper(value=30, step=5, lo=5, hi=120, suffix=" s"))
        muted = QLabel("silêncio mínimo p/ cortar")
        muted.setProperty("role", "muted")
        step_row.addWidget(muted)
        step_row.addStretch(1)
        lay.addLayout(step_row)

        lay.addWidget(widgets.make_entry("Buscar nas reuniões…"))

        status_row = QHBoxLayout()
        ok = QLabel("✓ pronto")
        ok.setProperty("role", "ok")
        warn = QLabel("● em call")
        warn.setProperty("role", "warn")
        status_row.addWidget(ok)
        status_row.addWidget(warn)
        status_row.addStretch(1)
        lay.addLayout(status_row)

        # chat estilo Claude Code desktop (NÃO terminal): fonte de UI, bolhas macias
        self._q = QLabel("Quais foram os bloqueios da call?")
        self._q.setObjectName("chatQ")
        self._q.setWordWrap(True)
        self._q.setMaximumWidth(300)
        self._a = QLabel("Dois: o boleto não gera em produção com CNPJ alfanumérico e falta "
                         "o de/para de centros — ambos dependem do time funcional.")
        self._a.setObjectName("chatA")
        self._a.setWordWrap(True)
        self._a.setMaximumWidth(330)
        lay.addWidget(self._q, 0, Qt.AlignRight)
        lay.addWidget(self._a, 0, Qt.AlignLeft)

        self.refresh()

    def refresh(self) -> None:
        """Reaplica o que não vem do QSS global: fundo translúcido com gradiente +
        backdrop acrílico (Mica), as bolhas de chat e a barra de título — tudo do tema."""
        t = theme.active()
        q_bg = theme._rgba(t.accent, 0.18)
        q_br = theme._rgba(t.accent, 0.45)
        self.setStyleSheet(
            f"#spikePanel {{ background: {theme.window_gradient(t, alpha=0.85)}; }}"
            f"QLabel#chatQ {{ background:{q_bg}; border:1px solid {q_br}; border-radius:13px;"
            f" padding:9px 12px; color:{t.text}; }}"
            f"QLabel#chatA {{ background:{t.surface}; border:1px solid {t.border}; border-radius:13px;"
            f" padding:9px 12px; color:{t.text}; }}"
        )
        widgets.enable_mica(self)           # backdrop translúcido do Win11
        widgets.enable_dark_titlebar(self)  # barra de título segue o tema


def _make_tray_icon() -> QIcon:
    """Ícone (bolinha na cor de gravação do tema) desenhado em memória — sem asset."""
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.active().rec))
    p.drawEllipse(6, 6, 20, 20)
    p.end()
    return QIcon(pm)


def _log_environment(pill: SpikePill) -> None:
    """Loga o setup multi-monitor + o tema ativo, para o Allan conferir na tela real
    (setup dele: 2 monitores 1080p; a pílula costuma morar no 2º, ~x=3482)."""
    primary = QApplication.primaryScreen()
    log.info("=== ambiente do spike ===")
    log.info("tema ativo: %s (%s)", theme.active().name, theme.active().label)
    log.info("padrão automático (segue o Windows agora): %s", theme.os_default_theme())
    for i, scr in enumerate(QApplication.screens()):
        g = scr.geometry()
        log.info(
            "monitor %d%s: lógico (x=%d y=%d w=%d h=%d) · devicePixelRatio=%.2f",
            i, "  [primário]" if scr is primary else "", g.x(), g.y(), g.width(), g.height(),
            scr.devicePixelRatio(),
        )
    log.info("overlay_pos legado (tkinter): %s", util.read_state().get("overlay_pos"))
    log.info("pílula colocada (lógico) em: [%d, %d]", pill.x(), pill.y())
    scr_at = QApplication.screenAt(pill.geometry().center())
    log.info("pílula está no monitor: %s",
             QApplication.screens().index(scr_at) if scr_at else "NENHUM (fora de tela!)")
    if any(s.devicePixelRatio() != 1.0 for s in QApplication.screens()):
        log.info("NOTA: há monitor com escala != 100% — a migração física->lógico do #53 importa aqui.")
    else:
        log.info("Todos a 100% (DPR=1): coords físicas do tkinter == lógicas do Qt, sem migração.")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = QApplication(sys.argv)
    theme.apply(app)

    pump = UiPump(interval_ms=100)
    pump.start()

    pill = SpikePill()
    pill.place()
    pill.show()
    if widgets.exclude_from_capture(pill):
        log.info("exclusão de captura: OK (a pílula NÃO aparece em print/compartilhamento)")
    else:
        log.warning("exclusão de captura: FALHOU (Windows < 10 2004? sandbox?) — segue visível")

    panel = SpikePanel()
    panel.show()
    panel.refresh()  # barra de título segue o tema (winId já existe após show)
    _log_environment(pill)

    tray = QSystemTrayIcon(_make_tray_icon())
    tray.setToolTip("ScribaDev (spike Qt)")
    menu = QMenu()

    # troca de tema ao vivo: reestiliza tudo (QSS global) + repinta pílula e painel
    theme_menu = menu.addMenu("Tema")
    group = QActionGroup(theme_menu)
    group.setExclusive(True)

    def switch(name: str) -> None:
        theme.set_active(name, app=app)  # persiste + reaplica o QSS global
        tray.setIcon(_make_tray_icon())
        pill.update()
        panel.refresh()
        log.info("tema trocado para: %s", name)

    for th in theme.themes():
        act = theme_menu.addAction(th.label)
        act.setCheckable(True)
        act.setChecked(th.name == theme.active().name)
        act.triggered.connect(lambda _checked, n=th.name: switch(n))
        group.addAction(act)

    menu.addSeparator()
    menu.addAction("Pílula: mostrar", pill.show)
    menu.addAction("Pílula: ocultar", pill.hide)
    menu.addSeparator()
    menu.addAction("Sair", app.quit)
    tray.setContextMenu(menu)
    tray.show()

    # thread de fundo: cronômetro + fases de status, tudo empurrado via pump.ui(...)
    stop = threading.Event()

    def worker() -> None:
        t0 = time.monotonic()
        phase = 0
        while not stop.is_set():
            elapsed = int(time.monotonic() - t0)
            # a cada ~8s alterna p/ um status fixo por 2s, depois volta ao cronômetro
            if elapsed and elapsed % 8 < 2:
                txt = ("finalizando…", "transcrevendo…")[phase % 2]
                pump.ui(lambda t=txt: pill.set_status(t))
                phase += 1
            else:
                mm, ss = divmod(elapsed, 60)
                pump.ui(lambda m=mm, s=ss: pill.set_elapsed(f"{m:02d}:{s:02d}"))
            time.sleep(1.0)

    threading.Thread(target=worker, daemon=True, name="spike-worker").start()

    # Desligamento limpo: para a thread de fundo, o timer do pump e esconde a bandeja
    # ANTES do Qt destruir os objetos C++. Sem isso, o PySide6 no Windows costuma
    # segfaultar na saída (destruição de QSystemTrayIcon/janelas fora de ordem).
    def _cleanup() -> None:
        stop.set()
        pump.stop()
        tray.hide()

    app.aboutToQuit.connect(_cleanup)

    log.info("spike rodando. Troque o tema na bandeja · arraste a pílula pelo corpo · × fecha.")
    return app.exec()


if __name__ == "__main__":
    import os

    rc = main()
    logging.shutdown()  # descarrega os logs antes do os._exit
    # os._exit pula o teardown do interpretador (e do Qt), que é onde o PySide6
    # segfaulta na saída no Windows. Padrão já usado no app (relaunch). O processo
    # morre limpo; o SO recupera os recursos.
    os._exit(rc)
