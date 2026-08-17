"""Sistema de temas do ScribaDev em Qt: tokens (cor, fonte, métrica) + QSS + troca a quente.

Cada tema é um conjunto FECHADO de tokens (um `Theme`); o QSS do app inteiro é
GERADO a partir do tema ativo, e os widgets pintados à mão (ToggleSwitch, a pílula)
leem `theme.active()` no paintEvent — trocar de tema reestiliza tudo, inclusive
fonte e fundo.

Decisões (épico #44, 2026-07-02):
- A paleta Qt NÃO espelha a do tkinter (redesenho com vibe de editor; contraste
  validado por teste em test_qt_theme). Encerra a invariante antiga "PALETTE ==".
- Fontes modernas: Inter (UI) + Cascadia Code (mono), com fallback gracioso.
- Fundo com leve gradiente (bg2 -> bg), não cor chapada; translucidez fica a cargo
  do backdrop nativo (widgets.enable_mica) nas janelas de verdade.
- Padrão = tema do Windows: escuro do sistema -> "vscode", claro -> "light". A
  escolha explícita do usuário (state.json `ui_theme`) sempre vence.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, fields

from .. import util

log = logging.getLogger("scriba.qt.theme")


@dataclass(frozen=True)
class Theme:
    """Conjunto fechado de tokens de um tema. Todo campo é obrigatório (sem default):
    um tema incompleto é erro, não um buraco silencioso preenchido por acaso."""

    name: str    # slug estável (persistido no state.json)
    label: str   # nome exibido na UI
    dark: bool    # é um tema escuro? (barra de título nativa + escolha por SO)

    # superfícies (do fundo da janela ao mais "elevado")
    bg: str          # fundo da janela (stop final do gradiente)
    bg2: str         # 2º stop do gradiente do fundo (leve variação de bg)
    surface: str     # painéis, cards, cabeçalhos
    field: str       # campos de entrada, code/log inset
    overlay: str     # tooltips, menus, popovers

    # bordas
    border: str
    border_strong: str

    # texto
    text: str        # primário
    muted: str       # secundário
    faint: str       # terciário / desabilitado

    # acento interativo/marca (botão primário, foco, links)
    accent: str
    accent_hover: str
    accent_press: str
    on_accent: str   # texto sobre o acento

    # semântica
    ok: str          # sucesso / verde
    warn: str        # âmbar: "em call", avisos não-críticos
    rec: str         # vermelho: gravando / perigo (identidade do app)
    rec_dim: str     # vermelho apagado (frame "off" do pulso)
    info: str

    # destaques de busca (fundo do hit + texto legível sobre ele)
    highlight: str
    highlight_current: str
    on_highlight: str

    # visualizador de log/código
    code_bg: str
    code_err: str

    # seleção de texto
    selection_bg: str
    selection_fg: str

    # tipografia
    font_family: str   # UI
    font_mono: str     # código / log
    font_size: int
    font_size_small: int  # textos secundários (hints/legendas); tokeniza o antigo 8pt hardcode

    # métricas
    radius: int      # raio de canto de botões/containers
    radius_sm: int   # raio menor (campos, chips)


# Fallbacks de fonte: se a preferida não estiver instalada, o Qt desce a lista.
# Inter + Cascadia Code cobrem o Windows 11 do Allan; o resto garante o mínimo.
_UI_FALLBACK = ["Segoe UI Variable", "Segoe UI", "sans-serif"]
_MONO_FALLBACK = ["Cascadia Mono", "Consolas", "monospace"]
_UI = "Inter"
_MONO = "Cascadia Code"


# ---------------------------------------------------------------- temas -------
# Cores desenhadas para contraste (o teste WCAG trava regressão). O vermelho fica
# reservado p/ gravação/perigo em TODOS os temas; o "accent" é a cor interativa e
# muda de tema pra tema. bg2 é uma variação sutil de bg (o gradiente do fundo).

_VSCODE = Theme(
    name="vscode", label="Escuro (VS Code)", dark=True,
    bg="#1f1f1f", bg2="#26262b", surface="#252526", field="#2d2d2d", overlay="#2b2b2b",
    border="#333333", border_strong="#454545",
    text="#d4d4d4", muted="#9d9d9d", faint="#6e6e6e",
    accent="#0e639c", accent_hover="#1177bb", accent_press="#0d5289", on_accent="#ffffff",
    ok="#4ec9b0", warn="#d7ba7d", rec="#f14c4c", rec_dim="#7a2626", info="#3794ff",
    highlight="#ffe14d", highlight_current="#ff8c1a", on_highlight="#1f1f1f",
    code_bg="#181818", code_err="#f14c4c",
    selection_bg="#264f78", selection_fg="#ffffff",
    font_family=_UI, font_mono=_MONO, font_size=9, font_size_small=8,
    radius=6, radius_sm=4,
)

_SUBLIME = Theme(
    name="sublime", label="Escuro (Sublime/Mariana)", dark=True,
    bg="#2d3540", bg2="#333e4c", surface="#343d46", field="#3b4553", overlay="#343d46",
    border="#3f4954", border_strong="#556071",
    text="#d8dee9", muted="#a6accd", faint="#7a8aa0",
    accent="#6699cc", accent_hover="#7aa8d6", accent_press="#5589bd", on_accent="#10151b",
    ok="#99c794", warn="#fac761", rec="#ec5f67", rec_dim="#7a3236", info="#5fb3b3",
    highlight="#fac761", highlight_current="#f9ae58", on_highlight="#2d3540",
    code_bg="#232a33", code_err="#ec5f67",
    selection_bg="#4e5a65", selection_fg="#ffffff",
    font_family=_UI, font_mono=_MONO, font_size=9, font_size_small=8,
    radius=6, radius_sm=4,
)

_CLAUDE = Theme(
    name="claude", label="Escuro (Claude)", dark=True,
    bg="#262624", bg2="#2d2a26", surface="#2f2e2b", field="#35342f", overlay="#2f2e2b",
    border="#3d3a33", border_strong="#514c40",
    text="#ece9e3", muted="#a39d91", faint="#736e62",
    accent="#b85c3c", accent_hover="#c96442", accent_press="#a04f33", on_accent="#ffffff",
    ok="#87a96b", warn="#d9a441", rec="#e5484d", rec_dim="#7a2f2c", info="#6a9ec9",
    highlight="#f2c94c", highlight_current="#f2994a", on_highlight="#262624",
    code_bg="#1f1e1c", code_err="#e5484d",
    selection_bg="#4a3b2f", selection_fg="#ffffff",
    font_family=_UI, font_mono=_MONO, font_size=9, font_size_small=8,
    radius=8, radius_sm=5,
)

# Claro (refinado, #70): camadas separadas por LUMINÂNCIA, não por 2% de cinza. A
# versão antiga tinha bg #fff e surface #f3f3f3 (quase iguais) — os cards sumiam no
# fundo. Aqui o fundo é cinza-claro e os cards/campos são brancos, que "flutuam"
# (padrão Win11/GitHub/VS Code Light). border mais definido separa sem peso.
_LIGHT = Theme(
    name="light", label="Claro", dark=False,
    bg="#f4f6f8", bg2="#eef1f6", surface="#ffffff", field="#ffffff", overlay="#ffffff",
    border="#dde1e6", border_strong="#b6bfca",
    text="#1a1d21", muted="#586170", faint="#8a929e",
    accent="#0067c0", accent_hover="#0078d4", accent_press="#005ba1", on_accent="#ffffff",
    ok="#1a7f37", warn="#8a5d00", rec="#c9302f", rec_dim="#e5a3a2", info="#0067c0",
    highlight="#ffd23f", highlight_current="#f08000", on_highlight="#1f1f1f",
    code_bg="#eef1f5", code_err="#c9302f",
    selection_bg="#cce4f7", selection_fg="#1f1f1f",
    font_family=_UI, font_mono=_MONO, font_size=9, font_size_small=8,
    radius=6, radius_sm=4,
)

_THEMES: dict[str, Theme] = {t.name: t for t in (_VSCODE, _SUBLIME, _CLAUDE, _LIGHT)}
DEFAULT_DARK = "vscode"   # tema quando o SO está no modo escuro
DEFAULT_LIGHT = "light"   # tema quando o SO está no modo claro

_STATE_KEY = "ui_theme"
_active: Theme | None = None  # cache do tema vigente (evita reler o state a cada paint)


# --------------------------------------------------------- padrão por SO ------

def _os_prefers_dark() -> bool:
    """O SO está no modo escuro para apps? Windows: registro AppsUseLightTheme
    (0=escuro, 1=claro). macOS: colorScheme do Qt (≥6.5), com fallback headless no
    `defaults read` (a chave AppleInterfaceStyle SÓ existe no modo escuro — erro ao
    ler = modo claro). Padrão escuro se indisponível (Linux / falha)."""
    if sys.platform == "darwin":
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QGuiApplication

            app = QGuiApplication.instance()
            if app is not None:
                scheme = app.styleHints().colorScheme()
                if scheme == Qt.ColorScheme.Dark:
                    return True
                if scheme == Qt.ColorScheme.Light:
                    return False
                # Unknown: cai para o defaults read abaixo
        except Exception:
            pass
        try:
            import subprocess

            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                 capture_output=True, timeout=5)
            return out.returncode == 0
        except Exception:
            return True
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        try:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        finally:
            winreg.CloseKey(key)
        return value == 0
    except Exception:
        return True


def os_default_theme() -> str:
    """Slug do tema que casa com o tema atual do SO (escuro->vscode, claro->light)."""
    return DEFAULT_DARK if _os_prefers_dark() else DEFAULT_LIGHT


# --------------------------------------------------------------- acesso -------

def themes() -> list[Theme]:
    """Todos os temas registrados, na ordem de exibição."""
    return list(_THEMES.values())


def by_slug(name: str) -> Theme:
    """Tema pelo slug; cai no padrão do SO se o slug for desconhecido/None."""
    return _THEMES.get(name, _THEMES[os_default_theme()])


def active() -> Theme:
    """Tema vigente. A escolha explícita do usuário (state.json) vence; na ausência
    dela, segue o tema do Windows. Lê o state na 1ª vez e cacheia."""
    global _active
    if _active is None:
        name = util.read_state().get(_STATE_KEY)
        if name is None:
            name = os_default_theme()
        _active = _zoomed(_THEMES.get(name, _THEMES[os_default_theme()]))
    return _active


def set_active(name: str, *, app=None) -> Theme:
    """Troca o tema vigente, persiste (escolha explícita) e reestiliza a quente se
    houver QApplication. Widgets pintados pegam o novo tema no próximo update()."""
    global _active
    _active = _zoomed(_THEMES.get(name, _THEMES[os_default_theme()]))
    util.update_state(**{_STATE_KEY: _active.name})
    if app is None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
    if app is not None:
        apply(app, _active)
    return _active


def clear_choice() -> None:
    """Volta ao padrão automático (segue o Windows): esquece a escolha explícita."""
    global _active
    _active = None
    st = util.read_state()
    if _STATE_KEY in st:
        st.pop(_STATE_KEY)
        util.atomic_write_text(util.STATE_PATH, __import__("json").dumps(st))


def current_choice() -> str | None:
    """Slug do tema escolhido EXPLICITAMENTE pelo usuário, ou None se está no modo
    automático (segue o Windows). Para a UI de seleção marcar o item vigente."""
    return util.read_state().get(_STATE_KEY)


def apply_choice(name: str | None, *, app=None) -> None:
    """Aplica a escolha de tema vinda da UI e reestiliza a quente: `name` = slug de um
    tema (persiste via set_active), ou None para o modo automático (clear_choice +
    reaplica o padrão do SO). Reuso pela aba Aparência e pelo menu da bandeja."""
    if name is not None:
        set_active(name, app=app)
        return
    clear_choice()
    if app is None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
    if app is not None:
        apply(app)


# ------------------------------------------------ tamanho da interface (zoom) --
# O Qt mapeia PONTOS a 72 dpi no macOS e 96 dpi no Windows/Linux — por isso os
# mesmos 9pt do tema "encolhem" ~25% no mac (#104). O zoom escala TODOS os pontos
# da UI: os tokens font_size/font_size_small do tema ativo (QSS) e os font-size
# inline/paints via zpt(). Troca A QUENTE pelo mesmo caminho do tema (apply +
# restyle_theme). NÃO usar QT_FONT_DPI: o QPA cocoa ignora a env (verificado).

_ZOOM_KEY = "ui_zoom"
_ZOOM_MIN, _ZOOM_MAX = 0.5, 3.0
# 133% ≈ 96/72: no mac, devolve exatamente a proporção visual do Windows
ZOOM_OPTIONS = (1.0, 1.10, 1.25, 96 / 72, 1.5, 1.75, 2.0)


def default_zoom() -> float:
    """Zoom padrão por SO: 133% no macOS (paridade visual com o Windows), 100% nos
    demais — quem atualiza no Windows não percebe NADA (identidade byte a byte)."""
    return 96 / 72 if sys.platform == "darwin" else 1.0


def zoom_choice() -> float | None:
    """Zoom escolhido EXPLICITAMENTE (state.json), ou None no modo automático."""
    try:
        z = float(util.read_state().get(_ZOOM_KEY))
    except (TypeError, ValueError):
        return None
    return z if _ZOOM_MIN <= z <= _ZOOM_MAX else None


def current_zoom() -> float:
    """Zoom vigente: escolha explícita ou o padrão do SO."""
    z = zoom_choice()
    return z if z is not None else default_zoom()


def zpt(pt: float) -> int:
    """Pontos de DESIGN → pontos vigentes (escala pelo zoom). Para os font-size
    inline e paints que não passam pelos tokens do tema. Com zoom 100% é a
    identidade — estilos do Windows saem byte-idênticos."""
    return max(1, round(pt * current_zoom()))


def _zoomed(t: Theme) -> Theme:
    """Cópia do tema com as fontes escaladas pelo zoom (identidade no 100% — o
    MESMO objeto volta, garantindo QSS byte-idêntico no Windows padrão)."""
    z = current_zoom()
    if abs(z - 1.0) < 0.005:
        return t
    from dataclasses import replace

    return replace(t, font_size=max(1, round(t.font_size * z)),
                   font_size_small=max(1, round(t.font_size_small * z)))


def set_zoom_choice(zoom: float | None, *, app=None) -> None:
    """Persiste a escolha (None = automático do SO) e reestiliza A QUENTE pelo
    mesmo caminho da troca de tema: tema ativo reconstruído + apply (QSS + fonte
    base + restyle_theme nas janelas vivas). Janelas sem restyle_theme atualizam
    ao serem reabertas."""
    global _active
    if zoom is None:
        st = util.read_state()
        if _ZOOM_KEY in st:
            st.pop(_ZOOM_KEY)
            util.atomic_write_text(util.STATE_PATH, __import__("json").dumps(st))
    else:
        util.update_state(**{_ZOOM_KEY: round(float(zoom), 3)})
    _active = None  # reconstrói com o novo zoom na próxima leitura
    if app is None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
    if app is not None:
        apply(app)


# ----------------------------------------------------------- tipografia -------

def qfont(theme: Theme | None = None, size: int | None = None, *, bold: bool = False,
          mono: bool = False):
    """QFont do tema com a cadeia de fallback certa (UI = Inter…, mono = Cascadia…)."""
    from PySide6.QtGui import QFont

    t = theme or active()
    f = QFont()
    f.setFamilies([t.font_mono, *_MONO_FALLBACK] if mono else [t.font_family, *_UI_FALLBACK])
    # `size` explícito é em pontos de DESIGN → escala pelo zoom (t.font_size já vem
    # escalado quando t é o tema ativo)
    f.setPointSize(zpt(size) if size else t.font_size)
    f.setBold(bold)
    return f


def _ui_stack(t: Theme) -> str:
    """Cadeia de fontes UI como string CSS (para o QSS)."""
    return ", ".join(f'"{fam}"' for fam in [t.font_family, *_UI_FALLBACK])


def _rgba(hex_color: str, alpha: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r}, {g}, {b}, {alpha})"


def _icon_path(name: str, svg: str) -> str:
    """Grava (1x) um SVG num cache gravável e devolve o path (forward slashes p/ o QSS).
    Arquivo em vez de data-uri porque o Qt só carrega `url()` do QSS de ARQUIVO, não de
    `data:image/svg` — sem isto combos/spins/date ficam sem ícone. Falha graciosa."""
    from pathlib import Path

    d = Path(util.STATE_PATH).parent / "qt_icons"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    path = d / name
    if not path.exists():
        try:
            path.write_text(svg, encoding="utf-8")
        except OSError:
            pass
    return str(path).replace("\\", "/")


def _arrow_icon_path(color: str, *, up: bool = False) -> str:
    """SVG de seta (▲/▼) na cor do tema — indicador de QComboBox e das setas de QSpinBox."""
    pts = "3,8 9,8 6,3.5" if up else "3,4 9,4 6,8.5"
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12'>"
           f"<polygon points='{pts}' fill='{color}'/></svg>")
    return _icon_path(f"arrow_{'up' if up else 'dn'}_{color.lstrip('#')}.svg", svg)


def _calendar_icon_path(color: str) -> str:
    """SVG de ícone de calendário na cor do tema — indicador dos QDateEdit (no lugar da seta)."""
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16'>"
           f"<rect x='2.2' y='3.4' width='11.6' height='10.4' rx='1.6' fill='none' stroke='{color}' stroke-width='1.3'/>"
           f"<line x1='2.2' y1='6.6' x2='13.8' y2='6.6' stroke='{color}' stroke-width='1.3'/>"
           f"<line x1='5.2' y1='1.9' x2='5.2' y2='4.2' stroke='{color}' stroke-width='1.4' stroke-linecap='round'/>"
           f"<line x1='10.8' y1='1.9' x2='10.8' y2='4.2' stroke='{color}' stroke-width='1.4' stroke-linecap='round'/>"
           f"</svg>")
    return _icon_path(f"calendar_{color.lstrip('#')}.svg", svg)


# --------------------------------------------------------- ícones Fluent ------
# Ícones vendorizados de github.com/microsoft/fluentui-system-icons (MIT; texto da
# licença em scriba/qt/icons/LICENSE). São SVGs de cor única (#212121 na origem);
# recolorimos para o token do tema e gravamos no MESMO cache de _icon_path. É só
# texto no repo: nenhuma dependência de runtime. Vocabulário em scriba/qt/icons/*.svg.

_FLUENT_SRC_COLOR = "#212121"


def icon(name: str, color: str | None = None) -> str:
    """Path (em cache) de um ícone Fluent recolorido para `color` (default: texto do
    tema). Serve tanto p/ `url()` do QSS quanto p/ QIcon (ver `qicon`). Devolve ''
    quando o ícone não existe (falha graciosa, sem levantar)."""
    from pathlib import Path

    src = Path(__file__).resolve().parent / "icons" / f"{name}.svg"
    try:
        svg = src.read_text(encoding="utf-8")
    except OSError:
        return ""
    col = color or active().text
    return _icon_path(f"fluent_{name}_{col.lstrip('#')}.svg", svg.replace(_FLUENT_SRC_COLOR, col))


def qicon(name: str, color: str | None = None):
    """QIcon do ícone Fluent recolorido (p/ QAbstractButton.setIcon / QTreeWidgetItem.
    setIcon). QIcon vazio se o ícone não existir. Nota: setIcon é estático — na troca
    de tema a quente o ícone só reflete a nova cor quando reconstruído/reaberto."""
    from PySide6.QtGui import QIcon

    p = icon(name, color)
    return QIcon(p) if p else QIcon()


def window_gradient(theme: Theme | None = None, *, alpha: float = 1.0) -> str:
    """Valor QSS de fundo com gradiente sutil (bg2 -> bg) para a raiz das janelas.
    `alpha` < 1 deixa o fundo translúcido (para o backdrop acrílico aparecer atrás;
    ver widgets.enable_mica). Uso: `root.setStyleSheet(f'#root {{ background: {g} }}')`."""
    t = theme or active()
    return (f"qlineargradient(x1:0, y1:0, x2:0.4, y2:1, "
            f"stop:0 {_rgba(t.bg2, alpha)}, stop:1 {_rgba(t.bg, alpha)})")


# --------------------------------------------------------------- QSS -----------

def qss(theme: Theme | None = None) -> str:
    """Folha de estilo global gerada a partir do tema. Cobre os controles-base;
    widgets pintados (ToggleSwitch, pílula) leem os tokens direto no paintEvent."""
    t = theme or active()
    ui = _ui_stack(t)
    field_radius = t.radius + 3  # campos mais macios/arredondados (não "terminal")
    arrow_dn = _arrow_icon_path(t.text)
    arrow_up = _arrow_icon_path(t.text, up=True)
    cal_icon = _calendar_icon_path(t.text)
    return f"""
    QWidget {{
        background-color: {t.bg};
        color: {t.text};
        font-family: {ui};
        font-size: {t.font_size}pt;
    }}

    QPushButton {{
        background-color: {t.surface};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: {t.radius}px;
        padding: 6px 16px;
        min-height: 18px;
    }}
    QPushButton:hover {{ background-color: {t.border}; }}
    QPushButton:pressed {{ background-color: {t.field}; }}
    QPushButton:disabled {{ color: {t.faint}; border-color: {t.field}; }}

    QPushButton[kind="primary"] {{
        background-color: {t.accent};
        color: {t.on_accent};
        border: none;
        font-weight: bold;
    }}
    QPushButton[kind="primary"]:hover {{ background-color: {t.accent_hover}; }}
    QPushButton[kind="primary"]:pressed {{ background-color: {t.accent_press}; }}

    /* botão de ícone (find, command bar, expander) — leve, com hover discreto */
    QToolButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: {t.radius}px;
        padding: 4px 6px;
        color: {t.text};
    }}
    QToolButton:hover {{ background-color: {t.surface}; border-color: {t.border}; }}
    QToolButton:pressed {{ background-color: {t.field}; }}
    QToolButton:checked {{ background-color: {t.field}; }}
    QToolButton::menu-indicator {{ image: none; }}

    /* expander (cabeçalho colapsável): a linha inteira é clicável, sem caixa */
    QToolButton[expander="true"] {{
        border: none; background: transparent;
        color: {t.accent_hover}; font-weight: bold; font-size: 11pt;
        padding: 2px 0;
    }}
    QToolButton[expander="true"]:hover {{ color: {t.accent}; background: transparent; }}

    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QTimeEdit {{
        background-color: {t.field};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: {field_radius}px;
        padding: 7px 11px;
        selection-background-color: {t.selection_bg};
        selection-color: {t.selection_fg};
    }}
    /* hover: sinaliza que o campo é editável antes de receber foco (ex.: título da nota,
       que sem isto parece um label). O foco (abaixo) vence quando ambos valem. */
    QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover, QComboBox:hover, QDateEdit:hover, QTimeEdit:hover {{
        border-color: {t.border_strong};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QComboBox:focus, QDateEdit:focus, QTimeEdit:focus {{
        border: 1px solid {t.accent};
    }}
    QLineEdit:disabled, QDateEdit:disabled, QTimeEdit:disabled, QComboBox:disabled {{
        color: {t.faint};
        background-color: {t.bg};
    }}

    QLabel {{ background: transparent; }}
    QLabel[role="muted"] {{ color: {t.muted}; }}
    QLabel[role="ok"]     {{ color: {t.ok}; }}
    QLabel[role="warn"]   {{ color: {t.warn}; }}

    QToolTip {{
        background-color: {t.overlay};
        color: {t.text};
        border: 1px solid {t.border_strong};
        padding: 5px 8px;
    }}

    QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {t.border}; border-radius: 6px; min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {t.border_strong}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
    QScrollBar::handle:horizontal {{
        background: {t.border}; border-radius: 6px; min-width: 24px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {t.border_strong}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* seta do dropdown (combo) e do calendário (QDateEdit calendarPopup): a área é
       clicável; o triângulo é desenhado pelo widgets._ArrowStyle na cor do tema. */
    /* seta do dropdown (combo) e do calendário (QDateEdit calendarPopup). A seta vem
       de um SVG em arquivo (theme._arrow_icon_path) porque o Qt não carrega data:svg. */
    QComboBox::drop-down, QDateEdit::drop-down {{
        subcontrol-origin: padding; subcontrol-position: center right;
        border: none; width: 22px; background: transparent;
    }}
    QComboBox::down-arrow {{ image: url({arrow_dn}); width: 12px; height: 12px; }}
    QDateEdit::down-arrow {{ image: url({cal_icon}); width: 15px; height: 15px; }}

    QSpinBox::up-button, QDoubleSpinBox::up-button, QTimeEdit::up-button {{
        subcontrol-origin: border; subcontrol-position: top right; width: 18px;
        border-left: 1px solid {t.border}; border-top-right-radius: {field_radius}px;
        background: {t.surface};
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button, QTimeEdit::down-button {{
        subcontrol-origin: border; subcontrol-position: bottom right; width: 18px;
        border-left: 1px solid {t.border}; border-bottom-right-radius: {field_radius}px;
        background: {t.surface};
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QTimeEdit::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover, QTimeEdit::down-button:hover {{
        background: {t.border};
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QTimeEdit::up-arrow {{ image: url({arrow_up}); width: 10px; height: 10px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QTimeEdit::down-arrow {{ image: url({arrow_dn}); width: 10px; height: 10px; }}

    QComboBox QAbstractItemView {{
        background-color: {t.overlay};
        color: {t.text};
        border: 1px solid {t.border_strong};
        selection-background-color: {t.accent};
        selection-color: {t.on_accent};
        outline: none;
    }}

    QCheckBox {{ background: transparent; spacing: 7px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {t.border_strong};
        border-radius: {t.radius_sm}px;
        background: {t.field};
    }}
    QCheckBox::indicator:hover {{ border-color: {t.accent}; }}
    QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}
    QCheckBox::indicator:disabled {{ border-color: {t.field}; }}

    QRadioButton {{ background: transparent; spacing: 7px; }}
    QRadioButton::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {t.border_strong};
        border-radius: 8px;
        background: {t.field};
    }}
    QRadioButton::indicator:hover {{ border-color: {t.accent}; }}
    QRadioButton::indicator:checked {{
        /* anel de acento com miolo claro; 8 + 2*4 de borda = os mesmos 16px do desmarcado */
        width: 8px; height: 8px;
        border: 4px solid {t.accent};
        border-radius: 8px;
        background: {t.on_accent};
    }}
    QRadioButton::indicator:disabled {{ border-color: {t.field}; }}

    QGroupBox {{
        background: transparent;
        border: 1px solid {t.border};
        border-radius: {t.radius}px;
        margin-top: 14px;
        padding: 10px 8px 6px 8px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
        color: {t.muted};
    }}

    QTabWidget::pane {{ border: 1px solid {t.border}; border-radius: {t.radius}px; top: -1px; }}
    QTabBar {{ background: transparent; }}
    QTabBar::tab {{
        background: {t.surface};
        color: {t.muted};
        border: 1px solid {t.border};
        border-bottom: none;
        border-top-left-radius: {t.radius_sm}px;
        border-top-right-radius: {t.radius_sm}px;
        padding: 6px 14px;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{ background: {t.field}; color: {t.text}; }}
    QTabBar::tab:hover {{ color: {t.text}; }}

    QProgressBar {{
        background: {t.field};
        border: 1px solid {t.border};
        border-radius: {t.radius_sm}px;
        text-align: center;
        color: {t.text};
    }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: {t.radius_sm}px; }}

    QTreeWidget, QTreeView {{
        background: {t.field};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: {t.radius_sm}px;
        outline: none;
    }}
    QTreeWidget::item {{ padding: 3px 2px; }}
    QTreeWidget::item:selected, QTreeView::item:selected {{ background: {t.accent}; color: {t.on_accent}; }}
    QTreeWidget::item:hover, QTreeView::item:hover {{ background: {t.surface}; }}
    /* selecionada E sob o mouse: sem esta regra o :hover (surface, quase o fundo)
       vence o :selected e a linha "perde" a seleção no Shift+clique da grade */
    QTreeWidget::item:selected:hover, QTreeView::item:selected:hover {{
        background: {t.accent_hover}; color: {t.on_accent};
    }}

    QSplitter::handle {{ background: {t.border}; }}
    QSplitter::handle:horizontal {{ width: 4px; }}
    QSplitter::handle:vertical {{ height: 4px; }}

    QCalendarWidget QWidget {{ alternate-background-color: {t.surface}; }}
    QCalendarWidget #qt_calendar_navigationbar {{ background: {t.surface}; }}
    QCalendarWidget QToolButton {{
        color: {t.text}; background: transparent; border: none;
        padding: 4px 10px; border-radius: {t.radius_sm}px;
    }}
    QCalendarWidget QToolButton:hover {{ background: {t.border}; }}
    QCalendarWidget QToolButton::menu-indicator {{ image: none; }}
    QCalendarWidget QMenu {{ background: {t.overlay}; color: {t.text}; }}
    QCalendarWidget QSpinBox {{ background: {t.field}; color: {t.text}; border: 1px solid {t.border}; }}
    QCalendarWidget QAbstractItemView:enabled {{
        background: {t.field}; color: {t.text};
        selection-background-color: {t.accent}; selection-color: {t.on_accent};
        outline: none;
    }}
    QCalendarWidget QAbstractItemView:disabled {{ color: {t.faint}; }}

    QMenu {{
        background-color: {t.overlay};
        color: {t.text};
        border: 1px solid {t.border_strong};
        padding: 4px;
    }}
    QMenu::item {{ padding: 5px 24px 5px 12px; border-radius: {t.radius_sm}px; }}
    QMenu::item:selected {{ background-color: {t.accent}; color: {t.on_accent}; }}
    QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}
    """


def _restyle_top_levels(app) -> None:
    """Depois de trocar o QSS global, EMPURRA o novo tema para as janelas abertas: cada
    top-level widget que exponha `restyle_theme()` re-aplica seus estilos INLINE (que o
    QSS global não cobre — bordas de acento, fundos de card etc. capturados na
    construção). Sem isto, trocar de tema a quente deixa resíduos do tema anterior
    (ex.: borda de card laranja ao sair do Claude; cards escuros no tema claro) — #70.
    Contrato leve e desacoplado: nada de sinais persistentes; só toca janelas VIVAS."""
    for w in app.topLevelWidgets():
        fn = getattr(w, "restyle_theme", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                log.exception("restyle_theme falhou em %s", type(w).__name__)


def app_icon():
    """QIcon do app (pergaminho): o .ico multi-resolução quando existe, senão o PNG."""
    from PySide6.QtGui import QIcon

    ico = util.ICON_ICO
    return QIcon(str(ico if ico.exists() else util.ICON_PNG))


def apply(app, theme: Theme | None = None) -> None:
    """Aplica fonte-base (Inter…) + QSS do tema ao QApplication e re-estiliza as janelas
    abertas (restyle_theme). Idempotente."""
    t = theme or active()
    app.setFont(qfont(t))
    app.setStyleSheet(qss(t))
    # ícone default de TODA janela top-level (wizard, Notas, Config…): apply() é o
    # chokepoint comum aos entry points (main, cli, harnesses) — sem isto, janela que
    # não chama setWindowIcon próprio abre com o ícone genérico do SO
    app.setWindowIcon(app_icon())
    _restyle_top_levels(app)


def token_names() -> list[str]:
    """Nomes dos tokens de cor (str '#rrggbb') — usado pelo teste de contraste/completude."""
    return [f.name for f in fields(Theme)
            if f.type == "str" and f.name not in ("name", "label", "font_family", "font_mono")]
