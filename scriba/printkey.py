"""Tecla de print configurável (#229).

O usuário escolhe a tecla nas Configurações (botão "Trocar…" captura a próxima
tecla pressionada). O padrão é a Print Screen. O valor no config é um spec em
texto: "printscreen", "f9", "ctrl+shift+p"... e vazio desliga.

Aqui fica só a parte pura (testável sem teclado): normalizar o spec, o rótulo que
a tela mostra e a tradução para cada sistema:
- Windows: modificadores + código virtual (VK), observados pelo gancho de teclado.
- macOS: modificadores Carbon + keycode, registrados como atalho global só
  enquanto a gravação dura. A "printscreen" no Mac são os atalhos do sistema
  (Cmd+Shift+3/4/5), que o observador da pasta de capturas já cobre; teclados
  de PC no Mac mandam a Print Screen como F13, que também é registrada.
"""

from __future__ import annotations

DEFAULT = "printscreen"

MODIFIERS = ("ctrl", "alt", "shift", "cmd", "win")

# nome -> (VK do Windows, keycode do macOS ou None)
_KEYS: dict[str, tuple[int, int | None]] = {
    "printscreen": (0x2C, 0x69),   # no Mac, teclado de PC: Print Screen = F13
    "scrolllock": (0x91, 0x6B),    # = F14 no Mac
    "pause": (0x13, 0x71),         # = F15 no Mac
    "insert": (0x2D, 0x72),        # = Help no Mac
    "delete": (0x2E, 0x75),
    "home": (0x24, 0x73),
    "end": (0x23, 0x77),
    "pageup": (0x21, 0x74),
    "pagedown": (0x22, 0x79),
    "space": (0x20, 0x31),
}
_MAC_F = {1: 0x7A, 2: 0x78, 3: 0x63, 4: 0x76, 5: 0x60, 6: 0x61, 7: 0x62, 8: 0x64, 9: 0x65,
          10: 0x6D, 11: 0x67, 12: 0x6F, 13: 0x69, 14: 0x6B, 15: 0x71, 16: 0x6A, 17: 0x40,
          18: 0x4F, 19: 0x50}
for _n in range(1, 25):
    _KEYS[f"f{_n}"] = (0x6F + _n, _MAC_F.get(_n))
_MAC_LETTERS = dict(a=0x00, s=0x01, d=0x02, f=0x03, h=0x04, g=0x05, z=0x06, x=0x07, c=0x08,
                    v=0x09, b=0x0B, q=0x0C, w=0x0D, e=0x0E, r=0x0F, y=0x10, t=0x11, o=0x1F,
                    u=0x20, i=0x22, p=0x23, l=0x25, j=0x26, k=0x28, n=0x2D, m=0x2E)
for _c in "abcdefghijklmnopqrstuvwxyz":
    _KEYS[_c] = (ord(_c.upper()), _MAC_LETTERS[_c])
_MAC_DIGITS = {"1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "5": 0x17, "6": 0x16, "7": 0x1A,
               "8": 0x1C, "9": 0x19, "0": 0x1D}
for _d in "0123456789":
    _KEYS[_d] = (ord(_d), _MAC_DIGITS[_d])

_LABELS = {"printscreen": "Print Screen", "scrolllock": "Scroll Lock", "pause": "Pause",
           "insert": "Insert", "delete": "Delete", "home": "Home", "end": "End",
           "pageup": "Page Up", "pagedown": "Page Down", "space": "Espaço",
           "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Cmd", "win": "Win"}


def parse(spec: str) -> tuple[frozenset[str], str] | None:
    """"ctrl+shift+p" -> ({"ctrl","shift"}, "p"). Uma tecla, modificadores opcionais
    para as teclas de função e especiais (Print Screen, F9...: a tecla solta é o caso
    normal); letras, números e espaço exigem Ctrl/Alt/Cmd/Win. None se vazio ou inválido."""
    mods: set[str] = set()
    key = None
    for tok in (spec or "").lower().replace(" ", "").split("+"):
        if not tok:
            continue
        if tok == "meta":
            tok = "win"
        if tok in MODIFIERS:
            mods.add(tok)
        elif tok in _KEYS and key is None:
            key = tok
        else:
            return None
    if key is None:
        return None
    if needs_modifier(key) and not (mods - {"shift"}):
        return None  # "p" sozinho viraria um print a cada letra digitada
    return frozenset(mods), key


def needs_modifier(key: str) -> bool:
    """Letras, números e espaço são de digitação: só valem com Ctrl/Alt/Cmd/Win."""
    return len(key) == 1 or key == "space"


def normalize(spec: str) -> str:
    """Spec canônico (modificadores em ordem fixa) ou "" se inválido/vazio."""
    p = parse(spec)
    if p is None:
        return ""
    mods, key = p
    return "+".join([m for m in MODIFIERS if m in mods] + [key])


def label(spec: str) -> str:
    """Texto da tela: "Print Screen", "Ctrl+Shift+P", "F9". Vazio -> "Desligada"."""
    p = parse(spec)
    if p is None:
        return "Desligada"
    mods, key = p
    parts = [_LABELS[m] for m in MODIFIERS if m in mods]
    parts.append(_LABELS.get(key, key.upper()))
    return "+".join(parts)


def is_default(spec: str) -> bool:
    return normalize(spec) == DEFAULT


# ------------------------------------------------------------------- Windows --

def win_binding(spec: str) -> tuple[frozenset[str], int] | None:
    """(modificadores, VK) para o gancho do Windows ("cmd" vale como "win")."""
    p = parse(spec)
    if p is None:
        return None
    mods, key = p
    mods = frozenset("win" if m == "cmd" else m for m in mods)
    return mods, _KEYS[key][0]


# --------------------------------------------------------------------- macOS --

_CARBON_MODS = {"ctrl": 0x1000, "alt": 0x0800, "shift": 0x0200, "cmd": 0x0100, "win": 0x0100}


def mac_binding(spec: str) -> tuple[int, int] | None:
    """(modificadores Carbon, keycode) para o atalho global do Mac; None se a tecla
    não existe no Mac."""
    p = parse(spec)
    if p is None:
        return None
    mods, key = p
    code = _KEYS[key][1]
    if code is None:
        return None
    m = 0
    for name in mods:
        m |= _CARBON_MODS[name]
    return m, code


# ------------------------------------------------------------------------ Qt --

def from_qt(qt_key: int, ctrl: bool, alt: bool, shift: bool, meta: bool, *, mac: bool) -> str:
    """Spec a partir de um evento de tecla do Qt (código Qt.Key_* em int). "" se a
    tecla não é suportada ou é só um modificador.

    No Mac o Qt troca os nomes: ControlModifier é a tecla Cmd e MetaModifier é a
    Control. `ctrl`/`meta` chegam como o Qt os nomeia."""
    from PySide6.QtCore import Qt

    k = int(qt_key)
    names = {int(Qt.Key_Print): "printscreen", int(Qt.Key_ScrollLock): "scrolllock",
             int(Qt.Key_Pause): "pause", int(Qt.Key_Insert): "insert", int(Qt.Key_Delete): "delete",
             int(Qt.Key_Home): "home", int(Qt.Key_End): "end", int(Qt.Key_PageUp): "pageup",
             int(Qt.Key_PageDown): "pagedown", int(Qt.Key_Space): "space"}
    key = names.get(k)
    if key is None and int(Qt.Key_F1) <= k <= int(Qt.Key_F24):
        key = f"f{k - int(Qt.Key_F1) + 1}"
    if key is None and int(Qt.Key_A) <= k <= int(Qt.Key_Z):
        key = chr(ord("a") + k - int(Qt.Key_A))
    if key is None and int(Qt.Key_0) <= k <= int(Qt.Key_9):
        key = chr(ord("0") + k - int(Qt.Key_0))
    if key is None:
        return ""
    mods = []
    if mac:
        if meta:
            mods.append("ctrl")
        if alt:
            mods.append("alt")
        if shift:
            mods.append("shift")
        if ctrl:
            mods.append("cmd")
    else:
        if ctrl:
            mods.append("ctrl")
        if alt:
            mods.append("alt")
        if shift:
            mods.append("shift")
        if meta:
            mods.append("win")
    return normalize("+".join(mods + [key]))
