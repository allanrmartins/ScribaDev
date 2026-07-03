"""Widgets-base do ScribaDev em Qt: equivalentes da fundação de `scriba/widgets.py`.

API deliberadamente análoga à do tkinter (ModernButton/ToggleSwitch/Stepper/
add_tooltip/flash_button/remember_geometry) para que a reescrita das janelas (fase
B) seja quase mecânica. Diferenças que o Qt resolve de graça e que aqui a gente
NÃO reimplementa à mão: cantos arredondados e hover/press dos botões (QSS em
theme.py), placeholder de campo (QLineEdit nativo) e tooltip escuro (QSS QToolTip).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (
    Property,
    QDate,
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QSize,
    Qt,
    QTime,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTimeEdit,
    QWidget,
)

from .. import util
from . import theme


# == barra de título escura (Win11) ===========================================

def enable_dark_titlebar(widget, caption_hex: str | None = None) -> None:
    """Barra de título na cor do tema (Windows 11; silencioso se indisponível).

    Segue o tema ativo: modo escuro/claro conforme `theme.dark`, cor da barra = fundo
    do tema (ou `caption_hex`). Porta `scriba/widgets.py:enable_dark_titlebar`; em Qt o
    `winId()` de um top-level JÁ é o HWND real (no Tk era a filha), então aplica direto
    — sem GetAncestor. Chamar DEPOIS de show() (o HWND precisa existir). Janelas
    frameless (a pílula) não têm barra: não chamar lá.
    """
    try:
        import ctypes

        t = theme.active()
        hwnd = int(widget.winId())
        if not hwnd:
            return

        def _set(attr: int, value: int) -> None:
            v = ctypes.c_int(value)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

        def _colorref(hex_color: str) -> int:
            r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
            return (b << 16) | (g << 8) | r

        _set(20, 1 if t.dark else 0)  # DWMWA_USE_IMMERSIVE_DARK_MODE
        _set(35, _colorref(caption_hex or t.bg))  # DWMWA_CAPTION_COLOR
        _set(36, _colorref(t.text))  # DWMWA_TEXT_COLOR
    except Exception:
        pass


# == backdrop translúcido (Mica/Acrylic, Win11) ===============================

# DWMWA_SYSTEMBACKDROP_TYPE (Win11 22H2+): material translúcido da janela.
# 2 = Mica (sutil, amostra o wallpaper) · 3 = Acrylic (mais translúcido/fosco).
_DWMWA_SYSTEMBACKDROP_TYPE = 38


def enable_mica(widget, *, acrylic: bool = False) -> bool:
    """Liga o backdrop translúcido nativo do Windows 11 na janela. Para o fosco
    APARECER, a raiz precisa de um fundo (semi)transparente — use
    `theme.window_gradient(alpha<1)`. Best-effort: falha graciosa em Win10/versões
    antigas. Chamar DEPOIS de show() (o HWND precisa existir)."""
    try:
        import ctypes

        value = ctypes.c_int(3 if acrylic else 2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(widget.winId()), _DWMWA_SYSTEMBACKDROP_TYPE, ctypes.byref(value), ctypes.sizeof(value)
        )
        return True
    except Exception:
        return False


# == exclusão de captura de tela (Win10 2004+) ================================

# WDA_EXCLUDEFROMCAPTURE: a janela some de QUALQUER captura/gravação/compartilhamento
# (Teams/Zoom/Meet/OBS/PrintScreen) mas segue visível localmente. É o que mantém a
# pílula fora da tela transmitida sem detectar "está compartilhando" (issues #9/#23).
_WDA_EXCLUDEFROMCAPTURE = 0x00000011


def exclude_from_capture(widget) -> bool:
    """Marca a janela como excluída de captura. True se aplicou. Falha graciosa
    (Windows < 10 2004, sandbox): a janela segue funcionando, só não fica oculta no
    compartilhamento. Chamar DEPOIS de show() (o HWND precisa existir).

    Porta `scriba/overlay.py:_exclude_from_capture`. Em Qt o `winId()` de um top-level
    já é o HWND real (no Tk era a janela-filha, que exigia GetAncestor(GA_ROOT))."""
    try:
        import ctypes
        import ctypes.wintypes as wt

        u32 = ctypes.windll.user32
        u32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
        u32.SetWindowDisplayAffinity.restype = wt.BOOL
        return bool(u32.SetWindowDisplayAffinity(int(widget.winId()), _WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        return False


# == botão ====================================================================

class ModernButton(QPushButton):
    """Botão arredondado com hover/press. kind: "secondary" (cinza) ou "primary" (accent).

    Cantos/hover/press vêm do QSS (theme.py:qss); aqui só marcamos a `kind` como
    propriedade dinâmica para o seletor `QPushButton[kind="primary"]` pegar.
    """

    def __init__(self, text: str, command: Callable[[], None] | None = None,
                 kind: str = "secondary", parent=None):
        super().__init__(text, parent)
        self.setProperty("kind", kind)
        self.setCursor(Qt.PointingHandCursor)
        if command is not None:
            self.clicked.connect(lambda: command())

    def set_text(self, text: str) -> None:
        """Alias de setText — paridade com a API tkinter (usado por flash_button)."""
        self.setText(text)


def flash_button(btn, text: str, revert_to: str, *, ms: int = 1500) -> None:
    """Feedback efêmero num botão: troca o texto por `text` e volta a `revert_to` após
    `ms`. Regra do app: nenhuma ação do usuário termina sem resposta visível."""
    try:
        btn.setText(text)
        QTimer.singleShot(ms, lambda: btn.setText(revert_to))
    except RuntimeError:
        pass  # botão já destruído


# == interruptor estilo Win11 =================================================

class ToggleSwitch(QAbstractButton):
    """Interruptor liga/desliga pintado (estilo Windows 11), com animação do knob.

    Substitui o ToggleSwitch em Canvas do tkinter. `checkable`; use `toggled(bool)`,
    `isChecked()` e `setChecked()`. O `knob` (0.0=desligado, 1.0=ligado) é uma Qt
    Property só para a QPropertyAnimation deslizar a bolinha.
    """

    W, H = 42, 22

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.W, self.H)
        self._knob = 1.0 if checked else 0.0
        self.setChecked(checked)
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(self.W, self.H)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_knob(self) -> float:
        return self._knob

    def set_knob(self, v: float) -> None:
        self._knob = v
        self.update()

    knob = Property(float, get_knob, set_knob)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # trilho: interpola border -> accent conforme o knob desliza (cores do tema ativo)
        t = theme.active()
        off = QColor(t.border)
        on = QColor(t.accent)
        t = self._knob
        rail = QColor(
            round(off.red() + (on.red() - off.red()) * t),
            round(off.green() + (on.green() - off.green()) * t),
            round(off.blue() + (on.blue() - off.blue()) * t),
        )
        p.setPen(Qt.NoPen)
        p.setBrush(rail)
        p.drawRoundedRect(1, 1, self.W - 2, self.H - 2, (self.H - 2) / 2, (self.H - 2) / 2)
        # bolinha
        r = (self.H - 10) / 2
        x_off = self.H // 2 + 1
        x_on = self.W - self.H // 2 - 1
        cx = x_off + (x_on - x_off) * t
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(cx - r, self.H / 2 - r, 2 * r, 2 * r)


# == campo com placeholder ====================================================

def make_entry(placeholder: str = "", parent=None) -> QLineEdit:
    """QLineEdit no tema (via QSS) com placeholder nativo. O placeholder do Qt some
    ao digitar e NÃO polui o valor lido (paridade com add_placeholder do tkinter)."""
    e = QLineEdit(parent)
    if placeholder:
        e.setPlaceholderText(placeholder)
    return e


# == filtros de data / hora opcionais =========================================

class DateFilter(QWidget):
    """Filtro de data OPCIONAL: um check "ativar" + QDateEdit com calendário popup.

    Desmarcado (padrão) = sem filtro; o QDateEdit fica desabilitado. Substitui os
    antigos QLineEdit "DD/MM/AAAA" (texto puro, sem validação, que falhavam em
    silêncio). Expõe `br()` -> 'DD/MM/AAAA' quando ligado e '' quando desligado, de
    modo que a lógica de filtro a jusante (que já trabalha com strings BR) não muda.
    Emite `changed()` em qualquer alteração (liga/desliga ou nova data).
    """

    changed = Signal()

    def __init__(self, label: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._chk = QCheckBox(label)
        self._chk.toggled.connect(self._on_toggle)
        self._edit = QDateEdit(QDate.currentDate())
        self._edit.setCalendarPopup(True)
        self._edit.setDisplayFormat("dd/MM/yyyy")
        self._edit.setEnabled(False)
        self._edit.dateChanged.connect(lambda _=None: self.changed.emit())
        lay.addWidget(self._chk)
        lay.addWidget(self._edit, 1)

    def _on_toggle(self, on: bool) -> None:
        self._edit.setEnabled(on)
        self.changed.emit()

    def br(self) -> str:
        """Data escolhida como 'DD/MM/AAAA', ou '' quando o filtro está desligado."""
        if not self._chk.isChecked():
            return ""
        return self._edit.date().toString("dd/MM/yyyy")

    def clear(self) -> None:
        """Desliga o filtro (paridade de chamada com QLineEdit.clear())."""
        self._chk.setChecked(False)


class TimeFilter(QWidget):
    """Filtro de hora mínima OPCIONAL: check "ativar" + QTimeEdit (HH:MM).

    Mesma ideia do DateFilter: desmarcado = sem filtro; `hhmm()` -> 'HH:MM' quando
    ligado, '' quando desligado. Emite `changed()`.
    """

    changed = Signal()

    def __init__(self, label: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._chk = QCheckBox(label)
        self._chk.toggled.connect(self._on_toggle)
        self._edit = QTimeEdit(QTime(0, 0))
        self._edit.setDisplayFormat("HH:mm")
        self._edit.setEnabled(False)
        self._edit.timeChanged.connect(lambda _=None: self.changed.emit())
        lay.addWidget(self._chk)
        lay.addWidget(self._edit, 1)

    def _on_toggle(self, on: bool) -> None:
        self._edit.setEnabled(on)
        self.changed.emit()

    def hhmm(self) -> str:
        """Hora escolhida como 'HH:MM', ou '' quando o filtro está desligado."""
        if not self._chk.isChecked():
            return ""
        return self._edit.time().toString("HH:mm")

    def clear(self) -> None:
        self._chk.setChecked(False)


# == stepper −/+ ==============================================================

class Stepper(QWidget):
    """Controle numérico −/+ com sufixo. Emite `valueChanged(int)`.

    Paridade com `scriba/widgets.py:Stepper` (que traçava uma IntVar); aqui o valor
    é interno e observável pelo sinal.
    """

    valueChanged = Signal(int)

    def __init__(self, value: int = 0, step: int = 5, lo: int = 0, hi: int = 600,
                 suffix: str = " s", parent=None):
        super().__init__(parent)
        self._value, self._step, self._lo, self._hi, self._suffix = value, step, lo, hi, suffix
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._minus = ModernButton("−", lambda: self._bump(-1))
        self._plus = ModernButton("+", lambda: self._bump(+1))
        for b in (self._minus, self._plus):
            b.setFixedWidth(30)
        self._label = QLabel(alignment=Qt.AlignCenter)
        self._label.setMinimumWidth(56)
        self._label.setStyleSheet("font-weight: bold;")
        lay.addWidget(self._minus)
        lay.addWidget(self._label)
        lay.addWidget(self._plus)
        self._sync()

    def _sync(self) -> None:
        self._label.setText(f"{self._value}{self._suffix}")

    def _bump(self, direction: int) -> None:
        self.setValue(self._value + direction * self._step)

    def value(self) -> int:
        return self._value

    def setValue(self, v: int) -> None:
        v = max(self._lo, min(self._hi, v))
        if v != self._value:
            self._value = v
            self._sync()
            self.valueChanged.emit(v)


# == tooltip ==================================================================

def add_tooltip(widget, text: str) -> None:
    """Tooltip escuro no hover. Em Qt é nativo (setToolTip) — o visual escuro vem do
    seletor `QToolTip` no QSS global. Mantido como função por paridade de chamada."""
    widget.setToolTip(text)


# == memória de geometria =====================================================

def remember_geometry(win, key: str, *, default: tuple[int, int, int, int] | None = None) -> None:
    """Restaura a geometria salva desta janela (state.json, chave `_geom_qt[key]`) ao
    abrir e a salva (debounce) quando o usuário move/redimensiona. Preserva tamanho/
    posição entre sessões. Silencioso em erro.

    Guarda [x, y, w, h] em pixels LÓGICOS (device-independent) do Qt, num namespace
    (`_geom_qt`) separado do `_geom` do tkinter — os formatos e o sistema de
    coordenadas diferem, então não se misturam durante a migração.
    """
    geoms = (util.read_state().get("_geom_qt") or {})
    saved = geoms.get(key)
    rect = saved if _is_geom(saved) else (list(default) if default else None)
    if rect:
        try:
            win.setGeometry(int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
        except Exception:
            pass

    debounce = QTimer(win)
    debounce.setSingleShot(True)
    debounce.setInterval(600)

    def _save() -> None:
        try:
            if not win.isVisible():
                return
            g = win.geometry()
        except RuntimeError:
            return
        rect = [g.x(), g.y(), g.width(), g.height()]
        cur = util.read_state().get("_geom_qt")
        cur = cur if isinstance(cur, dict) else {}
        if cur.get(key) != rect:
            cur[key] = rect
            util.update_state(_geom_qt=cur)

    debounce.timeout.connect(_save)

    # eventFilter em vez de subclasse: remember_geometry serve qualquer janela pronta.
    filt = _MoveResizeFilter(debounce)
    win.installEventFilter(filt)
    win._geom_filter = filt  # segura a referência (senão o GC leva o filtro)


def _is_geom(v) -> bool:
    return isinstance(v, (list, tuple)) and len(v) == 4 and all(isinstance(n, int) for n in v)


class _MoveResizeFilter(QObject):
    """Reinicia o debounce a cada Move/Resize da janela observada."""

    def __init__(self, debounce: QTimer):
        super().__init__()
        self._debounce = debounce

    def eventFilter(self, _obj, event) -> bool:
        if event.type() in (QEvent.Move, QEvent.Resize):
            self._debounce.start()
        return False  # nunca consome o evento
