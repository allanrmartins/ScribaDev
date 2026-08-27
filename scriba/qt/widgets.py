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
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTime,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractScrollArea,
    QApplication,
    QCheckBox,
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
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

    macOS (#104, M7): NSWindow.sharingType = None via qt/mac.py. GUARD obrigatório
    no platformName: sob QPA offscreen (CI) o winId() NÃO é um NSView — chamar o
    objc com esse valor derruba o processo.

    Porta `scriba/overlay.py:_exclude_from_capture`. Em Qt o `winId()` de um top-level
    já é o HWND real (no Tk era a janela-filha, que exigia GetAncestor(GA_ROOT))."""
    import sys

    if sys.platform == "darwin":
        try:
            from PySide6.QtGui import QGuiApplication

            if QGuiApplication.platformName() != "cocoa":
                return False
            from .mac import set_window_sharing_none

            return set_window_sharing_none(int(widget.winId()))
        except Exception:
            return False
    try:
        import ctypes
        import ctypes.wintypes as wt

        u32 = ctypes.windll.user32
        u32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
        u32.SetWindowDisplayAffinity.restype = wt.BOOL
        return bool(u32.SetWindowDisplayAffinity(int(widget.winId()), _WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        return False


# == topo sem roubar o foco ===================================================

def raise_without_activation(widget) -> bool:
    """Sobe a janela na ordem-Z SEM trazer o app para a frente. True se usou o
    caminho nativo do macOS.

    O `raise_()` do Qt não serve para uma janela que se re-assere sozinha: no QPA
    cocoa, `QCocoaWindow::raise()` termina em `[NSApp activateIgnoringOtherApps:YES]`.
    Como a pílula re-assere o topo a cada pulso (600 ms), o ScribaDev roubava o foco
    de volta durante a gravação inteira: dava para clicar no navegador/Meet só na
    fresta entre dois pulsos.

    É a segunda camada da defesa: `qt/__init__.py` já desliga a ativação embutida no
    raise (QT_MAC_SET_RAISE_PROCESS=0, a única saída para o raise IMPLÍCITO do
    `show()` de janelas Tool/ToolTip); aqui, nas chamadas que são nossas, mandamos
    `[NSWindow orderFront:]` direto — não dependemos daquela env. Se a ponte falhar,
    NÃO caímos no `raise_()`: ficar um degrau abaixo no empilhamento é cosmético (a
    janela já é WindowStaysOnTopHint), roubar o foco não é. Fora do macOS o
    `raise_()` não ativa o processo e segue valendo.
    """
    import sys

    if sys.platform != "darwin":
        widget.raise_()
        return False
    try:
        from PySide6.QtGui import QGuiApplication

        if QGuiApplication.platformName() != "cocoa":
            return False   # offscreen (CI): winId() não é NSView — chamar o objc derruba
        from .mac import order_front_no_activate

        return order_front_no_activate(int(widget.winId()))
    except Exception:
        return False


def bring_to_front(widget) -> None:
    """Traz a janela para a frente E dá foco a ela — o que o usuário espera ao abrir
    uma janela pela bandeja/atalho.

    No macOS isso exige ativar o processo explicitamente: o `raise_()` do Qt fazia
    isso sozinho, mas `qt/__init__.py` desligou esse efeito colateral
    (QT_MAC_SET_RAISE_PROCESS=0) justamente porque ele também disparava a reboque
    do `show()` da pílula e roubava o foco durante a gravação. Aqui a ativação é
    intencional, então é explícita.
    """
    import sys

    widget.raise_()
    widget.activateWindow()
    if sys.platform != "darwin":
        return
    try:
        from PySide6.QtGui import QGuiApplication

        if QGuiApplication.platformName() != "cocoa":
            return
        from .mac import activate_app

        activate_app()
    except Exception:
        pass


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


# == botão de ícone (command bar / find) ======================================

def icon_button(name: str, tooltip: str, command: Callable[[], None] | None = None,
                *, color: str | None = None, size: int = 18, parent=None) -> QToolButton:
    """QToolButton compacto, só-ícone (Fluent via `theme.qicon`) + tooltip. Para command
    bars e barras de busca, onde o texto ocuparia espaço demais e estouraria a linha.
    O visual (hover/raio) vem do QSS `QToolButton` do tema; `color` default = texto."""
    b = QToolButton(parent)
    b.setIcon(theme.qicon(name, color))
    b.setIconSize(QSize(size, size))
    b.setCursor(Qt.PointingHandCursor)
    if tooltip:
        b.setToolTip(tooltip)
    if command is not None:
        b.clicked.connect(lambda: command())
    return b


def flash_icon(btn, name: str, color: str, *, ms: int = 1500) -> None:
    """Feedback efêmero num botão de ícone: troca o ícone por `name` (cor `color`) e
    restaura o original após `ms`. Par do `flash_button` para botões só-ícone (a mesma
    regra: nenhuma ação termina sem resposta visível)."""
    try:
        old = btn.icon()
    except RuntimeError:
        return
    btn.setIcon(theme.qicon(name, color))

    def _restore() -> None:
        try:
            btn.setIcon(old)
        except RuntimeError:
            pass  # botão já destruído

    QTimer.singleShot(ms, _restore)


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


# == checkbox com tick verde + animação =======================================

def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


class AnimatedCheckBox(QCheckBox):
    """Checkbox com tick VERDE e animação ao marcar/desmarcar. Mantém a API do QCheckBox
    (setChecked/isChecked/toggled/text); só o indicador é desenhado à mão (paintEvent),
    com uma Property `fill` (0..1) animada — o tick surge/some e a borda vira verde. O
    QSS não anima, por isso o indicador é custom (mesmo caminho do ToggleSwitch)."""

    _BOX = 18   # lado do indicador
    _GAP = 9    # espaço indicador -> texto

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._fill = 1.0 if self.isChecked() else 0.0
        self._anim = QPropertyAnimation(self, b"fill", self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._fill)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_fill(self) -> float:
        return self._fill

    def set_fill(self, v: float) -> None:
        self._fill = v
        self.update()

    fill = Property(float, get_fill, set_fill)

    def sizeHint(self) -> QSize:
        # o texto é PINTADO à mão a partir de x = _BOX + _GAP (paintEvent): a largura
        # herdada do QCheckBox usa o indicador do ESTILO (~20px < 27px) e nascia curta —
        # o fim do rótulo era comido ("auto-atualiza…") em fontes mais largas
        base = super().sizeHint()
        w = self._BOX + 4   # box desenhado a partir de x=1 + folga à direita
        if self.text():
            w += self._GAP + self.fontMetrics().horizontalAdvance(self.text())
        return QSize(max(base.width(), w), max(base.height(), self._BOX + 6))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()   # encolher abaixo do hint = cortar o texto pintado

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)   # clicar em qualquer lugar (box ou texto) alterna

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        t = theme.active()
        f = max(0.0, min(1.0, self._fill))
        box = self._BOX
        rect = QRectF(1.0, (self.height() - box) / 2, box, box)

        border = _lerp_color(QColor(t.border_strong), QColor(t.ok), f)
        p.setPen(QPen(border, 1.6))
        p.setBrush(QColor(t.field))
        p.drawRoundedRect(rect, t.radius_sm, t.radius_sm)

        if f > 0.02:   # tick verde (✓) surgindo com f
            p.setOpacity(f)
            tick = QPen(QColor(t.ok), 2.2)
            tick.setCapStyle(Qt.RoundCap)
            tick.setJoinStyle(Qt.RoundJoin)
            p.setPen(tick)
            cx, cy = rect.center().x(), rect.center().y()
            p1 = QPointF(cx - 0.30 * box, cy + 0.02 * box)
            p2 = QPointF(cx - 0.06 * box, cy + 0.22 * box)
            p3 = QPointF(cx + 0.30 * box, cy - 0.22 * box)
            p.drawLine(p1, p2)
            p.drawLine(p2, p3)
            p.setOpacity(1.0)

        if self.text():
            p.setPen(QColor(t.text if self.isEnabled() else t.faint))
            tx = box + self._GAP
            p.drawText(QRectF(tx, 0, self.width() - tx, self.height()),
                       int(Qt.AlignVCenter | Qt.AlignLeft), self.text())


# == label com word-wrap que nunca é "comido" ================================

class WrapLabel(QLabel):
    """QLabel com word-wrap cujo texto NUNCA é cortado pelo layout no aperto.

    O QLabel embrulhado anuncia heightForWidth, mas quando está aninhado (ex.:
    dentro de um QFrame de card) o layout externo pode espremê-lo abaixo da altura
    do texto — o meio da frase some (visto na máquina de um usuário, fontes mais
    largas). A cada resize impõe minimumHeight = heightForWidth(largura atual):
    o aperto vai p/ os vizinhos que sabem ceder (scroll), nunca p/ o texto."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.setMinimumHeight(self.heightForWidth(max(1, self.width())))


# == campo com placeholder ====================================================

def make_entry(placeholder: str = "", parent=None) -> QLineEdit:
    """QLineEdit no tema (via QSS) com placeholder nativo. O placeholder do Qt some
    ao digitar e NÃO polui o valor lido (paridade com add_placeholder do tkinter)."""
    e = QLineEdit(parent)
    if placeholder:
        e.setPlaceholderText(placeholder)
    return e


# == scroll: campos não "roubam" a roda do mouse ==============================

class _WheelGuard(QObject):
    """Faz a roda do mouse rolar a PÁGINA (o QScrollArea ancestral) em vez de mudar o
    valor de um combo/spin/date sob o cursor que NÃO está com foco. Sem isso, rolar por
    cima de um campo num formulário longo altera o campo sem querer (queixa recorrente)."""

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Wheel and not obj.hasFocus():
            area = obj.parent()
            while area is not None and not isinstance(area, QAbstractScrollArea):
                area = area.parent()
            if area is not None:
                QApplication.sendEvent(area.viewport(), event)   # rola o formulário
            return True   # o campo não processa a roda
        return False


_wheel_guard = _WheelGuard()  # singleton stateless; a ref de módulo o mantém vivo


def no_wheel_steal(widget) -> None:
    """Impede `widget` (combo/spin/date) de capturar a roda a menos que esteja focado;
    a roda passa a rolar o formulário. Também remove o foco-por-roda (WheelFocus)."""
    widget.setFocusPolicy(Qt.StrongFocus)
    widget.installEventFilter(_wheel_guard)


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
        self._chk = AnimatedCheckBox(label)
        self._chk.toggled.connect(self._on_toggle)
        self._edit = QDateEdit(QDate.currentDate())
        self._edit.setCalendarPopup(True)
        self._edit.setDisplayFormat("dd/MM/yyyy")
        self._edit.setEnabled(False)
        no_wheel_steal(self._edit)
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
        self._chk = AnimatedCheckBox(label)
        self._chk.toggled.connect(self._on_toggle)
        self._edit = QTimeEdit(QTime(0, 0))
        self._edit.setDisplayFormat("HH:mm")
        self._edit.setEnabled(False)
        no_wheel_steal(self._edit)
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
        rect = _clamp_to_screen(rect)   # não abrir fora da tela se o monitor mudou (#61)
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


def _clamp_to_screen(rect):
    """Encaixa [x, y, w, h] na área útil de ALGUM monitor: se a geometria salva caiu fora
    da tela (monitor removido/reconfigurado entre sessões), recoloca dentro do work area do
    monitor mais próximo em vez de a janela abrir invisível. Best-effort (devolve o rect
    original em erro). Beneficia todas as janelas Qt que usam remember_geometry."""
    try:
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QGuiApplication

        x, y, w, h = (int(v) for v in rect)
        screen = QGuiApplication.screenAt(QPoint(x + w // 2, y + h // 2)) or QGuiApplication.primaryScreen()
        if screen is None:
            return rect
        a = screen.availableGeometry()
        w = min(w, a.width())
        h = min(h, a.height())
        x = min(max(x, a.left()), a.right() - w + 1)
        y = min(max(y, a.top()), a.bottom() - h + 1)
        return [x, y, w, h]
    except Exception:
        return rect


def remember_splitter(split, key: str, *, left_ratio: float = 0.28,
                      left_min: int = 240, left_max: int = 360) -> None:
    """Persiste a divisão de um QSplitter (state.json, `_geom_qt[key]` em base64) e a
    restaura ao abrir. Sem estado salvo, aplica um default PROPORCIONAL à largura com
    clamp no painel esquerdo (`left_min..left_max`) em vez de um tamanho fixo. Salva com
    debounce a cada arraste. Silencioso em erro. Mesma infra (state.json) do
    remember_geometry — não usa QSettings."""
    from PySide6.QtCore import QByteArray

    geoms = util.read_state().get("_geom_qt") or {}
    saved = geoms.get(key)
    restored = False
    if isinstance(saved, str):
        try:
            restored = bool(split.restoreState(QByteArray.fromBase64(saved.encode("ascii"))))
        except Exception:
            restored = False
    if not restored:
        def _apply_default() -> None:
            try:
                total = split.width() or sum(split.sizes()) or 1040
                left = int(max(left_min, min(left_max, total * left_ratio)))
                split.setSizes([left, max(1, total - left)])
            except RuntimeError:
                pass
        QTimer.singleShot(0, _apply_default)

    debounce = QTimer(split)
    debounce.setSingleShot(True)
    debounce.setInterval(500)

    def _save() -> None:
        try:
            b = bytes(split.saveState().toBase64()).decode("ascii")
        except Exception:
            return
        cur = util.read_state().get("_geom_qt")
        cur = cur if isinstance(cur, dict) else {}
        if cur.get(key) != b:
            cur[key] = b
            util.update_state(_geom_qt=cur)

    debounce.timeout.connect(_save)
    split.splitterMoved.connect(lambda *_: debounce.start())
    split._splitter_debounce = debounce   # segura a referência (senão o GC leva o timer)


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


# == seção colapsável (expander) ==============================================

class Collapsible(QWidget):
    """Cabeçalho clicável (chevron Fluent) + conteúdo colapsável. Expander da
    fundação: a linha INTEIRA é o alvo de clique (QToolButton `expander`, QSS em
    theme.py), com hover; o chevron gira (direita->baixo) ao abrir. Reusável:
    filtros de Notas (#65), "Presentes", e "Serviços" na capa (#56)."""

    def __init__(self, title: str):
        super().__init__()
        self._open = False
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 6); lay.setSpacing(2)
        self._hdr = QToolButton()
        self._hdr.setProperty("expander", True)
        self._hdr.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._hdr.setCursor(Qt.PointingHandCursor)
        self._hdr.setIconSize(QSize(14, 14))
        self._hdr.clicked.connect(self._toggle)
        lay.addWidget(self._hdr, 0, Qt.AlignLeft)
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
        self._hdr.setText(f"  {self._title}")
        self._hdr.setIcon(theme.qicon("chevron-down" if self._open else "chevron-right",
                                      color=theme.active().accent_hover))

    def _toggle(self) -> None:
        self._open = not self._open
        self._render_hdr()
        if self._content is not None:
            self._content.setVisible(self._open)


# == submenu "Reprocessar" (#186) =============================================

def reprocess_submenu(menu, folder, app):
    """Anexa o submenu "Reprocessar" a um menu de contexto de reunião (#186).

    Fonte única para a capa (reunião recente) e a janela de Notas (nota pronta e
    reunião falhada): mesmas opções, mesma habilitação. As duas ações:
      - Refazer o resumo -> CLI `summarize` (barato: reusa o transcript.json);
      - Refazer tudo     -> CLI `process` (re-transcreve do áudio + nota nova).
    "Refazer a transcrição" separado não existe de propósito: pela UI o resultado
    visível é a NOTA, então re-transcrever sempre termina regenerando a nota -
    que é exatamente o que o `process` faz.

    Cada opção só habilita com o insumo vivo (resumo pede transcript.json; tudo
    pede áudio ainda em disco - retenção e keep_audio=false apagam), e o motivo
    fica no tooltip da opção desabilitada. Reunião com .lock ativo, em
    processamento ou já na fila ganha o submenu inteiro desabilitado. Sem
    meta.json legível (ex.: pasta apagada), nada é anexado. Devolve o QMenu
    criado, ou None.
    """
    import json
    from pathlib import Path

    from PySide6.QtWidgets import QMenu

    folder = Path(folder)
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    # parent explícito no construtor: menu.addMenu(icon, título) deixaria a
    # ownership do C++ com o wrapper Python retornado - o submenu morria junto
    # com a variável local e o clique abria um menu já deletado (libshiboken)
    sub = QMenu("Reprocessar", menu)
    sub.setIcon(theme.qicon("refresh"))
    menu.addMenu(sub)
    sub.setToolTipsVisible(True)
    resumo = sub.addAction(theme.qicon("sparkle"), "Refazer o resumo",
                           lambda: app.enqueue_reprocess(folder, "summarize"))
    tudo = sub.addAction(theme.qicon("refresh"), "Refazer tudo",
                         lambda: app.enqueue_reprocess(folder, "process"))

    if util.is_locked(folder):
        busy = "Esta reunião está sendo processada agora."
    elif (meta.get("status") in util.IN_PROGRESS_STATUSES
          or getattr(app, "is_reprocess_queued", lambda _f: False)(folder)):
        busy = "Esta reunião já está na fila de processamento."
    else:
        busy = None
    if busy:
        for a in (resumo, tudo):
            a.setEnabled(False)
            a.setToolTip(busy)
        return sub

    if (folder / "transcript.json").exists():
        resumo.setToolTip("Rápido: reusa a transcrição já feita e regenera a nota. "
                          "A nota atual fica guardada como .bak.")
    else:
        resumo.setEnabled(False)
        resumo.setToolTip("Sem transcrição salva (transcript.json) nesta gravação.")
    if util.has_audio(folder, meta):
        tudo.setToolTip("Transcreve o áudio de novo e regenera a nota. "
                        "A nota atual fica guardada como .bak.")
    else:
        tudo.setEnabled(False)
        tudo.setToolTip("O áudio desta gravação já foi apagado "
                        "(retenção ou keep_audio desligado).")
    return sub
