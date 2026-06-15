"""Detecção de calls (Teams, Zoom, e reuniões no navegador) via registro do Windows.

O Windows rastreia o uso do microfone por aplicativo em
HKCU\\Software\\...\\CapabilityAccessManager\\ConsentStore\\microphone.
Enquanto o app está com o microfone aberto, o valor LastUsedTimeStop fica 0 —
isso vale inclusive com o mic mutado no Teams (o mute é por software).

Para apps desktop (Teams, Zoom) o registro basta. Para o navegador ele só diz
"chrome.exe está com o mic aberto" — qualquer site faz isso —, então a call
web (Meet, Teams web, Zoom web...) é confirmada pelo título de uma janela do
navegador (browser_titles). Uma vez confirmada, a call segue viva enquanto o
mic estiver aberto: trocar de aba não a derruba.
"""

from __future__ import annotations

import re
import time
import winreg
from enum import Enum
from typing import Callable

from .config import Detection

_BASE = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


class CallState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    GRACE = "grace"


_FRIENDLY = ("Teams", "Zoom", "Meet", "Webex", "Slack", "Discord")


def patterns_from(cfg: Detection) -> list[str]:
    pats = [p.strip().lower() for p in (cfg.apps or "").split(",") if p.strip()]
    if cfg.registry_key:  # configs antigas (v0.1) com chave única
        pats.append(cfg.registry_key.lower())
    return pats or ["teams"]


def browser_patterns_from(cfg: Detection) -> list[str]:
    """Basenames (sem .exe) dos navegadores monitorados; vazio = camada web desligada."""
    raw = getattr(cfg, "browsers", "") or ""
    return [p.strip().lower().removesuffix(".exe") for p in raw.split(",") if p.strip()]


def title_patterns_from(cfg: Detection) -> list[str]:
    """Padrões de título que confirmam reunião no navegador; vazio = qualquer mic conta."""
    raw = getattr(cfg, "browser_titles", "") or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def friendly_name(pattern_or_key: str) -> str:
    low = pattern_or_key.lower()
    for name in _FRIENDLY:
        if name.lower() in low:
            return name
    return pattern_or_key.split("_")[0].capitalize()


def _iter_mic_keys():
    """(nome_da_subchave, LastUsedTimeStop) para cada app rastreado pelo Windows."""
    for base in (_BASE, _BASE + r"\NonPackaged"):
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                    i += 1
                except OSError:
                    break
                if sub == "NonPackaged":
                    continue
                try:
                    with winreg.OpenKey(root, sub) as k:
                        stop, _ = winreg.QueryValueEx(k, "LastUsedTimeStop")
                except OSError:
                    continue
                yield sub, stop


def active_app(patterns: list[str]) -> str | None:
    """Nome amigável do app monitorado que está com o mic aberto agora, ou None."""
    for sub, stop in _iter_mic_keys():
        if stop != 0:
            continue
        low = sub.lower()
        for p in patterns:
            if p in low:
                return friendly_name(sub if len(p) < 6 else p)
    return None


def app_key_status(patterns: list[str]) -> dict[str, bool]:
    """{nome amigável: já existe chave no registro?} — para o doctor."""
    found = {friendly_name(p): False for p in patterns}
    for sub, _stop in _iter_mic_keys():
        low = sub.lower()
        for p in patterns:
            if p in low:
                found[friendly_name(p)] = True
    return found


def _key_basename(sub: str) -> str:
    """Basename do exe numa subchave NonPackaged (o caminho vem com '#')."""
    return sub.rsplit("#", 1)[-1].lower()


def active_browser(browser_pats: list[str]) -> str | None:
    """Basename (sem .exe) do navegador monitorado com o mic aberto agora, ou None.

    Match exato do executável: "msedge" não pode casar o msedgewebview2.exe
    que apps desktop (Teams, Outlook) embutem.
    """
    for sub, stop in _iter_mic_keys():
        if stop != 0:
            continue
        base = _key_basename(sub)
        for p in browser_pats:
            if base == p + ".exe":
                return p
    return None


def browser_key_status(browser_pats: list[str]) -> dict[str, bool]:
    """{navegador: já existe chave de mic no registro?} — para o doctor."""
    found = {p: False for p in browser_pats}
    for sub, _stop in _iter_mic_keys():
        base = _key_basename(sub)
        for p in browser_pats:
            if base == p + ".exe":
                found[p] = True
    return found


def web_service_label(title_pattern: str, desktop_names: set[str] | None = None) -> str:
    """Nome de exibição de um serviço web nos status: "Google Meet", "Teams web".

    Serviços que também são monitorados como app desktop (Teams, Zoom) ganham o
    sufixo "web" para as duas linhas não se confundirem.
    """
    name = friendly_name(title_pattern)
    if name == "Meet":
        return "Google Meet"
    if desktop_names and name in desktop_names:
        return f"{name} web"
    return name


def desktop_names_from(cfg: Detection) -> set[str]:
    """Nomes amigáveis dos apps desktop monitorados (para rotular os serviços web)."""
    return {friendly_name(p) for p in patterns_from(cfg)}


def _title_match(pattern: str, title: str) -> bool:
    """Substring por palavra inteira, sem caixa: 'Meet' casa 'Meet – xyz', não 'Meeting'."""
    return re.search(rf"(?<!\w){re.escape(pattern)}(?!\w)", title, re.IGNORECASE) is not None


def browser_call_service(browser: str, title_pats: list[str]) -> str | None:
    """Serviço de reunião confirmado pelo título de uma janela do navegador, ou None.

    Sem padrões (browser_titles = ""), qualquer uso de mic no navegador conta
    como call — o nome exibido vira o do próprio navegador.
    """
    if not title_pats:
        return browser.capitalize()
    from .wintitles import window_titles

    for title in window_titles({browser + ".exe"}):
        for p in title_pats:
            if _title_match(p, title):
                return friendly_name(p)
    return None


# -------- nome da reunião pelo título da janela (Teams/Zoom/navegador) ----------
# Robusto e barato: só lê títulos de janela (Win32), sem depender de acessibilidade.
# É só uma PISTA de contexto pro resumo — "" quando não dá pra extrair nada útil.

_TITLE_APP_SUFFIXES = (
    " - Google Chrome", " - Microsoft Edge", " - Mozilla Firefox", " - Brave",
    " - Opera", " - Vivaldi", " - Zoom", " - Zoom Meeting", " - Zoom Workplace",
)
_TITLE_NAV_PARTS = {
    "chat", "atividade", "activity", "equipes", "teams", "calendário", "calendar",
    "arquivos", "chamadas", "comunidades", "feed",
}
_TITLE_GENERIC = {
    "microsoft teams", "teams", "zoom", "zoom meeting", "zoom workplace", "meet",
    "google meet", "microsoft edge", "google chrome", "mozilla firefox", "brave",
    "opera", "vivaldi", "reunião", "meeting", "",
}


def _clean_meeting_title(raw: str) -> str:
    """Reduz um título de janela ao nome da reunião; "" se sobrar só app genérico."""
    t = re.sub(r"^\(\d+\)\s*", "", (raw or "").strip())  # contador de não-lidas
    t = re.sub(r"\s+(?:and|e)\s+\d+\s+more\s+pages?\.*$", "", t, flags=re.IGNORECASE)
    for suf in _TITLE_APP_SUFFIXES:
        if t.endswith(suf):
            t = t[: -len(suf)].strip()
            break
    if "|" in t:  # formato Teams: "Chat | <nome> | Microsoft Teams"
        parts = [p.strip() for p in t.split("|") if p.strip()]
        parts = [p for p in parts if p.lower() not in _TITLE_NAV_PARTS and p.lower() not in _TITLE_GENERIC]
        t = " — ".join(parts)
    t = re.sub(r"^(?:Meet|Reunião)\s*[–-]\s+", "", t)  # "Meet - xyz" -> "xyz"
    t = re.sub(r"\s+", " ", t).strip(" -–—|·\t")
    return "" if t.lower() in _TITLE_GENERIC else t


def capture_meeting_title(cfg: Detection) -> str:
    """Nome da reunião lido do título da janela do app da call (Teams/Zoom/navegador).

    Prefere a aba do navegador que casa um padrão de reunião; senão, a janela do
    Teams/Zoom. Retorna "" se nada confiável — captura de título NUNCA atrapalha a
    gravação (toda falha é engolida).
    """
    from .wintitles import window_titles

    try:
        browsers = browser_patterns_from(cfg)
        title_pats = title_patterns_from(cfg)
        if browsers and title_pats:
            for t in window_titles({b + ".exe" for b in browsers}):
                if any(_title_match(p, t) for p in title_pats):
                    name = _clean_meeting_title(t)
                    if name:
                        return name
        for exes in ({"ms-teams.exe", "teams.exe"}, {"zoom.exe"}):
            for t in window_titles(exes):
                name = _clean_meeting_title(t)
                if name:
                    return name
    except Exception:
        pass
    return ""


class Detector:
    """Máquina de estados que vigia o registro e emite início/fim de call.

    IDLE --mic em uso--> RECORDING --mic liberado--> GRACE --timeout--> IDLE (call_ended)
                                          ^--mic em uso de novo (troca de fone etc.)
    """

    def __init__(
        self,
        cfg: Detection,
        on_call_started: Callable[[], None],
        on_call_ended: Callable[[], None],
        on_grace: Callable[[], None] | None = None,
        on_grace_cancel: Callable[[], None] | None = None,
    ):
        self.cfg = cfg
        self.patterns = patterns_from(cfg)
        self.browser_pats = browser_patterns_from(cfg)
        self.title_pats = title_patterns_from(cfg)
        self.current_app: str | None = None  # app da call ativa (para status/meta)
        # call web já confirmada por título: daí em diante o mic aberto basta
        # (a pessoa pode trocar de aba sem derrubar a detecção)
        self._web_call = False
        self.on_call_started = on_call_started
        self.on_call_ended = on_call_ended
        self.on_grace = on_grace  # mic liberado: esperando a tolerância
        self.on_grace_cancel = on_grace_cancel  # mic voltou: era troca de device
        self.state = CallState.IDLE
        self._grace_deadline = 0.0
        self._recording_since = 0.0

    def _sense_call(self) -> str | None:
        """Quem está em call agora: app desktop, ou navegador com call confirmada."""
        app = active_app(self.patterns)
        if app:
            return app
        if not self.browser_pats:
            return None
        browser = active_browser(self.browser_pats)
        if browser is None:
            return None
        if self._web_call:
            return self.current_app or browser.capitalize()
        svc = browser_call_service(browser, self.title_pats)
        if svc:
            self._web_call = True
            return svc
        return None  # mic aberto no navegador, mas nenhum título de reunião (ainda)

    def poll_once(self) -> None:
        now = time.monotonic()
        app = self._sense_call()
        in_use = app is not None
        if app:
            self.current_app = app

        if self.state is CallState.IDLE:
            if in_use:
                self.state = CallState.RECORDING
                self._recording_since = now
                self.on_call_started()
        elif self.state is CallState.RECORDING:
            if not in_use:
                self.state = CallState.GRACE
                self._grace_deadline = now + self.cfg.grace_seconds
                if self.on_grace:
                    self.on_grace()
            elif now - self._recording_since > self.cfg.max_call_hours * 3600:
                # parada de segurança: encerra; se o mic seguir em uso, um novo
                # segmento começa no próximo poll
                self.state = CallState.IDLE
                self.on_call_ended()
        elif self.state is CallState.GRACE:
            if in_use:
                self.state = CallState.RECORDING
                if self.on_grace_cancel:
                    self.on_grace_cancel()
            elif now >= self._grace_deadline:
                self.state = CallState.IDLE
                self.on_call_ended()
                self.current_app = None
                self._web_call = False

    def run(self, stop_event) -> None:
        """Loop de detecção (roda em thread própria)."""
        while not stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                pass  # nunca derruba o loop; tenta de novo no próximo poll
            stop_event.wait(self.cfg.poll_seconds)
        if self.state is not CallState.IDLE:
            self.state = CallState.IDLE
            self._web_call = False
            self.on_call_ended()


def debug_loop() -> int:
    """`scriba detect`: imprime as transições de estado para teste manual."""
    from .config import load

    cfg = load().detection
    pats = patterns_from(cfg)
    status = app_key_status(pats)
    for name, exists in status.items():
        marker = "OK" if exists else "ainda sem chave no registro (entre numa call dele uma vez)"
        print(f"  {name}: {marker}")
    bpats = browser_patterns_from(cfg)
    tpats = title_patterns_from(cfg)
    watching = list(status)
    if bpats:
        ready = [b for b, ok in browser_key_status(bpats).items() if ok]
        print(f"  Navegadores: {', '.join(bpats)} (com chave de mic: {', '.join(ready) or 'nenhum ainda'})")
        print(f"  Títulos que confirmam call web: {', '.join(tpats) or '(qualquer site com mic)'}")
        watching += [friendly_name(t) for t in tpats] or ["navegador"]
    print(f"Vigiando {', '.join(dict.fromkeys(watching))} a cada {cfg.poll_seconds}s. Ctrl+C para sair.")

    def stamp(msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}")

    det = Detector(
        cfg,
        on_call_started=lambda: stamp(">>> CALL INICIADA - a gravação começaria agora"),
        on_call_ended=lambda: stamp("<<< CALL ENCERRADA - a transcrição começaria agora"),
    )
    last = det.state
    pending: str | None = None  # navegador com mic aberto, call ainda não confirmada
    try:
        while True:
            det.poll_once()
            if det.state is not last:
                stamp(f"estado: {last.value} -> {det.state.value} ({det.current_app or '-'})")
                last = det.state
            if bpats and det.state is CallState.IDLE:
                b = active_browser(bpats)
                if b != pending:
                    if b:
                        stamp(f"mic aberto em {b} — aguardando um título de reunião ({', '.join(tpats)})")
                    pending = b
            else:
                pending = None
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        print("\nencerrado.")
        return 0
