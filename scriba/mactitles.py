"""Títulos das janelas de um conjunto de apps no macOS, via AX API (#104, M4).

Backend darwin do `wintitles._enum_titles` — o wrapper com prazo (#114) e o
contrato `window_titles({"chrome.exe"}) -> list[str]` continuam em wintitles.py;
aqui só a enumeração crua. Os nomes chegam no formato do Windows (basename com
.exe) e são mapeados para bundle ids.

Precisa da permissão de Acessibilidade (uma vez, para o processo responsável —
no dev, o terminal). Sem ela, devolve [] e a detecção degrada como desenhado:
apps desktop (Teams/Zoom) detectam 100%; a camada de call no NAVEGADOR fica
muda e o nome da reunião sai vazio. `available()` alimenta o doctor.
"""

from __future__ import annotations

import logging

log = logging.getLogger("scriba.mactitles")

# basename (sem .exe) -> prefixos de bundle id (minúsculos)
_BUNDLE_PREFIXES: dict[str, tuple[str, ...]] = {
    "chrome": ("com.google.chrome",),
    "msedge": ("com.microsoft.edgemac",),
    "firefox": ("org.mozilla.firefox",),
    "brave": ("com.brave.browser",),
    "opera": ("com.operasoftware",),
    "vivaldi": ("com.vivaldi",),
    "safari": ("com.apple.safari",),
    "ms-teams": ("com.microsoft.teams",),
    "teams": ("com.microsoft.teams",),
    "zoom": ("us.zoom.xos",),
}


def available(prompt: bool = False) -> bool:
    """A permissão de Acessibilidade está concedida? Com prompt=True, pede ao
    usuário (abre o diálogo do sistema uma única vez)."""
    try:
        import ApplicationServices as AS

        if not prompt:
            return bool(AS.AXIsProcessTrusted())
        opts = {getattr(AS, "kAXTrustedCheckOptionPrompt", "AXTrustedCheckOptionPrompt"): True}
        return bool(AS.AXIsProcessTrustedWithOptions(opts))
    except Exception:
        return False


def _prefixes_for(exe_names: set[str]) -> tuple[str, ...]:
    out: list[str] = []
    for exe in exe_names:
        base = exe.lower().removesuffix(".exe")
        out.extend(_BUNDLE_PREFIXES.get(base, (base,)))
    return tuple(out)


def enum_titles(exe_names: set[str]) -> list[str]:
    """Enumeração crua (pode bloquear se um app-alvo estiver travado — só chamar
    via wintitles.window_titles, que aplica o prazo do #114)."""
    import ApplicationServices as AS
    from AppKit import NSWorkspace

    if not AS.AXIsProcessTrusted():
        return []
    kwin = getattr(AS, "kAXWindowsAttribute", "AXWindows")
    ktitle = getattr(AS, "kAXTitleAttribute", "AXTitle")
    prefixes = _prefixes_for(exe_names)
    titles: list[str] = []
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        bid = (app.bundleIdentifier() or "").lower()
        if not bid or not any(bid.startswith(p) for p in prefixes):
            continue
        ax = AS.AXUIElementCreateApplication(app.processIdentifier())
        err, wins = AS.AXUIElementCopyAttributeValue(ax, kwin, None)
        if err or not wins:
            continue
        for w in wins:
            err2, title = AS.AXUIElementCopyAttributeValue(w, ktitle, None)
            if not err2 and title:
                titles.append(str(title))
    return titles
