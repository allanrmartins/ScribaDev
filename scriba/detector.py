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

No macOS (#104, M4) a MESMA máquina de estados roda sobre os process objects do
CoreAudio (micusage_mac sintetiza os carimbos no formato FILETIME) e os títulos
vêm da AX API (mactitles, atrás do wintitles.window_titles de sempre).
"""

from __future__ import annotations

import logging
import re
import sys
import time
from enum import Enum
from typing import Callable

from .config import Detection

_BASE = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"

log = logging.getLogger("scriba.detector")

# a cada quantos polls reamostrar o título da janela durante a gravação (#35).
# 5 polls x poll_seconds(2s) ~= 10 s; EnumWindows é barato (a camada web já paga).
_TITLE_SAMPLE_EVERY = 5


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
    """(subchave, LastUsedTimeStart, LastUsedTimeStop) para cada app rastreado.

    Os dois tempos são FILETIME (100 ns desde 1601). LastUsedTimeStop == 0 enquanto
    o app está com o mic aberto. LastUsedTimeStart é reescrito toda vez que o app
    REABRE o mic — é o sinal de fronteira entre duas calls consecutivas (#34).
    """
    # macOS (#104, M4): mesma interface, fonte = process objects do CoreAudio
    # (micusage_mac sintetiza os carimbos em unidades FILETIME)
    if sys.platform == "darwin":
        from . import micusage_mac

        yield from micusage_mac.snapshot()
        return
    # lazy: winreg só existe no Windows; o import no topo quebrava o módulo (e a
    # cadeia scriba.main) em Linux/macOS antes de qualquer código rodar (#96)
    import winreg

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
                        try:
                            start, _ = winreg.QueryValueEx(k, "LastUsedTimeStart")
                        except OSError:
                            start = 0  # chave sem o valor (raro): trata como desconhecido
                except OSError:
                    continue
                yield sub, start, stop


def active_sessions(patterns: list[str]) -> dict[str, tuple[int, int]]:
    """{subchave: (LastUsedTimeStart, LastUsedTimeStop)} das subchaves que casam
    algum pattern (substring, como active_app). É a base para o detector rastrear
    a sessão de mic que sustenta a call e medir o gap por FILETIME (#34)."""
    out: dict[str, tuple[int, int]] = {}
    for sub, start, stop in _iter_mic_keys():
        low = sub.lower()
        if any(p in low for p in patterns):
            out[sub] = (start, stop)
    return out


def active_app(patterns: list[str]) -> str | None:
    """Nome amigável do app monitorado que está com o mic aberto agora, ou None."""
    for sub, _start, stop in _iter_mic_keys():
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
    for sub, _start, _stop in _iter_mic_keys():
        low = sub.lower()
        for p in patterns:
            if p in low:
                found[friendly_name(p)] = True
    return found


def _key_basename(sub: str) -> str:
    """Basename do exe numa subchave NonPackaged (o caminho vem com '#')."""
    return sub.rsplit("#", 1)[-1].lower()


# darwin: pattern de navegador (nome estilo Windows) -> prefixos de bundle id.
# Prefixo, e não igualdade: o áudio do Chrome roda num helper
# ("com.google.chrome.helper..."). Sem tabela p/ um pattern, usa o próprio nome.
_MAC_BROWSER_BUNDLES: dict[str, tuple[str, ...]] = {
    "chrome": ("com.google.chrome",),
    "msedge": ("com.microsoft.edgemac",),
    "firefox": ("org.mozilla.firefox",),
    "brave": ("com.brave.browser",),
    "opera": ("com.operasoftware",),
    "vivaldi": ("com.vivaldi",),
    "safari": ("com.apple.safari",),  # fora do default: mic via WebKit.GPU (ver micusage_mac)
}


def _browser_key_match(sub: str, pattern: str) -> bool:
    """A chave de sessão de mic `sub` é DO navegador `pattern`? Match exato por SO:
    no Windows compara o basename do exe ("msedge" não pode casar o
    msedgewebview2.exe que Teams/Outlook embutem); no darwin compara o bundle id
    pela tabela acima (a chave do micusage_mac é o próprio bundle)."""
    if sys.platform == "darwin":
        bundle = sub.lower()
        prefixes = _MAC_BROWSER_BUNDLES.get(pattern, (pattern,))
        return any(bundle == p or bundle.startswith(p + ".") for p in prefixes)
    return _key_basename(sub) == pattern + ".exe"


def active_browser(browser_pats: list[str]) -> str | None:
    """Basename (sem .exe) do navegador monitorado com o mic aberto agora, ou None.

    Match exato do executável: "msedge" não pode casar o msedgewebview2.exe
    que apps desktop (Teams, Outlook) embutem.
    """
    for sub, _start, stop in _iter_mic_keys():
        if stop != 0:
            continue
        for p in browser_pats:
            if _browser_key_match(sub, p):
                return p
    return None


def browser_key_status(browser_pats: list[str]) -> dict[str, bool]:
    """{navegador: já existe chave de mic no registro?} — para o doctor."""
    found = {p: False for p in browser_pats}
    for sub, _start, _stop in _iter_mic_keys():
        for p in browser_pats:
            if _browser_key_match(sub, p):
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
        on_call_started: Callable[..., None],  # aceita probe=bool (split pula a sonda)
        on_call_ended: Callable[[], None],
        on_grace: Callable[[], None] | None = None,
        on_grace_cancel: Callable[[], None] | None = None,
        on_title: Callable[[str], None] | None = None,
        is_recording: Callable[[], bool] | None = None,
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
        self.on_title = on_title  # título estável capturado (bônus #35: meta tardio)
        self.is_recording = is_recording  # #38: p/ rearmar o auto-record após ■
        self.state = CallState.IDLE
        self._grace_deadline = 0.0
        self._grace_since = 0.0  # entrada no GRACE (para medir o gap do resume)
        self._recording_since = 0.0
        self._last_exc_log = 0.0  # rate-limit do log de exceções do loop
        # sessão de mic que sustenta a call rastreada (para dividir calls seguidas, #34)
        self._session_sub: str | None = None   # subchave do registro
        self._session_start = 0                 # LastUsedTimeStart (FILETIME) no início
        self._session_stop = 0                  # LastUsedTimeStop guardado ao entrar no GRACE
        # sinal de título da reunião (#35): desempata splits ambíguos do #34
        self._title_last = ""        # última amostra de título
        self._title_stable = ""      # título estável (2 amostras iguais e não-vazias)
        self._title_at_grace = ""    # título estável guardado ao entrar no GRACE (Uso A)
        self._polls_since_title = 0  # contador para reamostrar a cada _TITLE_SAMPLE_EVERY

    def _mic_sessions(self) -> dict[str, tuple[int, int]]:
        """{subchave: (start, stop)} das sessões de mic de apps e navegadores
        monitorados — a matéria-prima para detectar a call e rastrear sua sessão."""
        sessions = active_sessions(self.patterns)  # apps desktop (substring)
        if self.browser_pats:  # navegadores: match exato por SO (não pega webview embutido)
            for sub, start, stop in _iter_mic_keys():
                if any(_browser_key_match(sub, p) for p in self.browser_pats):
                    sessions[sub] = (start, stop)
        return sessions

    def _sense(self, sessions: dict[str, tuple[int, int]]) -> tuple[str, str, int] | None:
        """(nome amigável, subchave, LastUsedTimeStart) da call ativa agora, ou None.

        App desktop tem prioridade; senão, navegador com reunião confirmada por
        título (uma vez confirmada, o mic aberto basta — trocar de aba não derruba).
        """
        for sub, (start, stop) in sessions.items():
            if stop != 0:
                continue
            low = sub.lower()
            for p in self.patterns:
                if p in low:
                    return friendly_name(sub if len(p) < 6 else p), sub, start
        if not self.browser_pats:
            return None
        for sub, (start, stop) in sessions.items():
            if stop != 0:
                continue
            for p in self.browser_pats:
                if not _browser_key_match(sub, p):
                    continue
                if self._web_call:
                    return (self.current_app or p.capitalize()), sub, start
                svc = browser_call_service(p, self.title_pats)
                if svc:
                    self._web_call = True
                    return svc, sub, start
                return None  # mic no navegador, mas nenhum título de reunião ainda
        return None

    def _sample_title(self) -> None:
        """Reamostra o título da janela a cada _TITLE_SAMPLE_EVERY polls e mantém o
        'título estável' (2 amostras iguais e não-vazias). Uso C (#35): se o estável
        muda sem nenhum evento de sessão de mic, só alerta - a v1 não divide por isso."""
        self._polls_since_title += 1
        if self._polls_since_title < _TITLE_SAMPLE_EVERY:
            return
        self._polls_since_title = 0
        t = capture_meeting_title(self.cfg)
        if t and t == self._title_last and t != self._title_stable:  # 2 amostras iguais
            if self._title_stable:  # Uso C: estável mudou sem ciclo/gap de mic
                log.warning("possível nova call sem ciclo de mic: %s -> %s", self._title_stable, t)
            self._title_stable = t
            if self.on_title:  # bônus: preenche o meeting_title tardio no meta
                self.on_title(t)
        self._title_last = t

    def poll_once(self) -> None:
        now = time.monotonic()
        sessions = self._mic_sessions()
        sense = self._sense(sessions)
        in_use = sense is not None
        prev_app = self.current_app  # antes da atualização, para os logs de troca
        if sense:
            self.current_app = sense[0]

        if self.state is CallState.IDLE:
            if in_use:
                self._begin(now, sense)

        elif self.state is CallState.RECORDING:
            self._sample_title()  # #35: reamostra o título da janela ~a cada 10 s
            if not in_use:
                # mic liberado -> GRACE. Guarda o LastUsedTimeStop EXATO da subchave
                # rastreada: o instante em que o app fechou o mic, sem depender do poll.
                self.state = CallState.GRACE
                self._grace_since = now
                self._grace_deadline = now + self.cfg.grace_seconds
                self._session_stop = sessions.get(self._session_sub or "", (0, 0))[1]
                self._title_at_grace = self._title_stable  # título de antes do gap (Uso A)
                log.info(
                    "mic liberado (%s) - tolerância de %.0fs",
                    self.current_app, self.cfg.grace_seconds,
                )
                if self.on_grace:
                    self.on_grace()
            else:
                self._watch_recording(now, sense, sessions, prev_app)

        elif self.state is CallState.GRACE:
            if in_use:
                self._resume_or_split(now, sense, sessions, prev_app)
            elif now >= self._grace_deadline:
                log.info("call encerrada (mic livre > %.0fs)", self.cfg.grace_seconds)
                self._end_call()

    def _watch_recording(
        self, now: float, sense: tuple[str, str, int],
        sessions: dict[str, tuple[int, int]], prev_app: str | None,
    ) -> None:
        """RECORDING com o mic ainda em uso: detecta nova call sem passar pelo GRACE
        (Teams->Zoom sem gap) e o ciclo de sessão invisível (mic reabriu < poll)."""
        _name, sub, start = sense
        tracked = sessions.get(self._session_sub or "")
        if self._session_sub and sub != self._session_sub:
            # a call agora é sustentada por OUTRA subchave monitorada
            if tracked is not None and tracked[1] != 0 and self.cfg.split_gap_seconds > 0:
                # a rastreada fechou o mic e outra assumiu -> Teams->Zoom sem gap
                log.info(
                    "app trocou (%s livre, %s em uso) - dividindo a gravação",
                    prev_app, sense[0],
                )
                self._split(now, sessions)
                return
            if self._rearm_if_stopped(sense):  # ■ dado: a call nova grava sozinha
                return
            log.warning("troca de app monitorado durante a call: %s -> %s", prev_app, sense[0])
            self._session_sub, self._session_start = sub, start
        elif tracked is not None and tracked[1] == 0 and start != self._session_start:
            # MESMA subchave, mas o start foi reescrito: o mic reabriu entre dois polls
            # (gap < poll). O registro não distingue de uma troca de device — #35 usa
            # o título de desempate: mudou => call nova; igual/vazio => não divide.
            now_title = capture_meeting_title(self.cfg)
            if (self.cfg.split_gap_seconds > 0 and self._title_stable and now_title
                    and now_title != self._title_stable):
                log.info(
                    "ciclo invisível + título mudou (%s -> %s) - dividindo a gravação",
                    self._title_stable, now_title,
                )
                self._split(now, sessions)
                return
            if self._rearm_if_stopped(sense):  # ■ dado: a call nova grava sozinha
                return
            log.warning(
                "ciclo de sessão invisível em %s (start mudou) - nao divido na v1",
                self.current_app,
            )
            self._session_start = start
        elif now - self._recording_since > self.cfg.max_call_hours * 3600:
            log.info("call encerrada (limite de segurança de %gh)", self.cfg.max_call_hours)
            self._end_call()

    def _resume_or_split(
        self, now: float, sense: tuple[str, str, int],
        sessions: dict[str, tuple[int, int]], prev_app: str | None,
    ) -> None:
        """GRACE + mic em uso de novo: emendar na mesma gravação (troca de fone) ou
        dividir (call nova). A decisão usa a subchave e o gap por FILETIME (#34, item 4)."""
        name, sub, start = sense
        gap_wall = now - self._grace_since  # fallback: relógio de parede (o gap de #36)
        split_on = self.cfg.split_gap_seconds > 0

        if self._session_sub and sub != self._session_sub:
            # mic voltou em OUTRA subchave monitorada -> nova call
            if split_on:
                log.info("mic voltou em app diferente (%s -> %s) - dividindo a gravação", prev_app, name)
                self._split(now, sessions)
                return
            log.warning(
                "mic voltou em app diferente após %.1fs: %s -> %s (split desligado)",
                gap_wall, prev_app, name,
            )
        else:
            # mesma subchave: gap por FILETIME (cai no relógio de parede se falhar a leitura)
            gap = self._filetime_gap(start, self._session_stop)
            gap = gap_wall if gap is None else gap
            if split_on and gap >= self.cfg.split_gap_seconds:
                # #35: título igual ao de antes do gap => mesma reunião (troca de fone
                # lenta) => suprime o split. Em dúvida NÃO divide (fusão é recuperável).
                now_title = capture_meeting_title(self.cfg)
                if self._title_at_grace and now_title and now_title == self._title_at_grace:
                    log.info(
                        "gap de %.1fs, mas título inalterado (%s) - NÃO divido (mesma reunião)",
                        gap, now_title,
                    )
                else:
                    log.info(
                        "gap de %.1fs >= split_gap_seconds=%g - dividindo a gravação",
                        gap, self.cfg.split_gap_seconds,
                    )
                    self._split(now, sessions)
                    return
            else:
                log.info("mic voltou após %.1fs - mesma call", gap)

        # emenda (resume): atualiza o rastreamento e volta a RECORDING
        self.state = CallState.RECORDING
        self._session_sub, self._session_start = sub, start
        if self.on_grace_cancel:
            self.on_grace_cancel()

    def _begin(self, now: float, sense: tuple[str, str, int], probe: bool = True) -> None:
        """IDLE -> RECORDING: começa a rastrear a sessão de mic que sustenta a call."""
        name, sub, start = sense
        self.current_app = name
        self.state = CallState.RECORDING
        self._recording_since = now
        self._session_sub, self._session_start = sub, start
        # nova call: zera o rastreamento de título (#35)
        self._title_last = self._title_stable = self._title_at_grace = ""
        self._polls_since_title = 0
        self.on_call_started(probe=probe)

    def _rearm_if_stopped(self, sense: tuple[str, str, int]) -> bool:
        """Rearma o auto-record quando um NOVO ciclo de sessão de mic começa mas não
        há gravação em andamento — o usuário deu ■ com a call ainda ativa (#38). Fecha
        a armadilha do "■ e esqueci o ⏺": a call seguinte passa a gravar sozinha."""
        if not (self.cfg.auto_record and self.is_recording and not self.is_recording()):
            return False
        name, sub, start = sense
        log.info("auto-record re-armado: novo ciclo de mic sem gravação em andamento (%s)", name)
        self._session_sub, self._session_start = sub, start
        self.on_call_started(probe=False)
        return True

    def _end_call(self) -> None:
        """Encerra a call e volta a IDLE (timeout do GRACE ou parada de segurança)."""
        self.state = CallState.IDLE
        self.on_call_ended()
        self.current_app = None
        self._web_call = False
        self._session_sub = None

    def _split(self, now: float, sessions: dict[str, tuple[int, int]]) -> None:
        """Encerra a call atual e, se o mic segue em uso, começa OUTRA na MESMA
        iteração — duas pastas, duas notas. A parte 1 segue o fluxo normal (fila)."""
        self.on_call_ended()
        self.state = CallState.IDLE
        self.current_app = None
        self._web_call = False       # call web nova reconfirma pelo título
        self._session_sub = None
        sense = self._sense(sessions)
        if sense:
            # sonda de áudio dispensada: os dispositivos funcionavam há < poll_seconds
            self._begin(now, sense, probe=False)

    @staticmethod
    def _filetime_gap(start_new: int, stop_old: int) -> float | None:
        """Gap em segundos entre o fim de uma sessão de mic e o início da próxima,
        por FILETIME (100 ns desde 1601). None se algum dos tempos é desconhecido."""
        if not start_new or not stop_old:
            return None
        return (start_new - stop_old) / 1e7

    def run(self, stop_event) -> None:
        """Loop de detecção (roda em thread própria)."""
        if sys.platform not in ("win32", "darwin"):
            # Windows: ConsentStore do registro; macOS: process objects do CoreAudio
            # (micusage_mac); Linux é um marco futuro (#104)
            log.info("detecção automática de calls não suportada neste SO ainda — use a gravação manual")
            return
        while not stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                # nunca derruba o loop; mas não engole mudo: loga no máximo 1x/min
                # (se o registro do Windows falhar em loop, o log não inunda)
                now = time.monotonic()
                if now - self._last_exc_log >= 60:
                    self._last_exc_log = now
                    log.exception("falha no poll do detector")
            stop_event.wait(self.cfg.poll_seconds)
        if self.state is not CallState.IDLE:
            self.state = CallState.IDLE
            self._web_call = False
            self._session_sub = None
            self.on_call_ended()


def debug_loop() -> int:
    """`scriba detect`: imprime as transições de estado para teste manual."""
    if sys.platform not in ("win32", "darwin"):
        print("detecção automática de calls não suportada neste SO ainda")
        return 1
    from .config import load

    # espelha os logs INFO/WARN do detector (GRACE, resume com o gap medido,
    # encerramento com o motivo, troca de app) no console
    root = logging.getLogger()
    if not root.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(h)
        root.setLevel(logging.INFO)

    cfg = load().detection
    pats = patterns_from(cfg)
    status = app_key_status(pats)
    for name, exists in status.items():
        if exists:
            marker = "OK"
        elif sys.platform == "darwin":
            marker = "nenhum uso de mic observado ainda nesta sessão"
        else:
            marker = "ainda sem chave no registro (entre numa call dele uma vez)"
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
        on_call_started=lambda *a, **k: stamp(">>> CALL INICIADA - a gravação começaria agora"),
        on_call_ended=lambda: stamp("<<< CALL ENCERRADA - a transcrição começaria agora"),
    )
    pending: str | None = None  # navegador com mic aberto, call ainda não confirmada
    try:
        while True:
            det.poll_once()
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
