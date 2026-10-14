"""Quem abriu o microfone numa call do Teams desktop - sugestão do nº de vozes.

O fim da call pergunta "quantas pessoas, além de você?" para travar a diarização
em N vozes. Repetir o número da call anterior erra nos dois sentidos: numa call
grande quase todos só escutam (56 participantes, 16 abriram o mic - 2026-10-14);
nas do dia a dia quase todos falam. Quem falou é o que importa.

A janela da call expõe isso na árvore de acessibilidade (ver uia_win), em inglês:
  bloco de vídeo:  "Fulano External unfamiliar, video is on, muted, Context menu is available"
  painel People:   "Fulano, Muted" / "Fulano External unfamiliar, Unmuted"
  quem fala:       "Fulano is speaking" (visto com a vista compacta aberta)
  perfil:          "Profile picture of Fulano." (avatar da janela principal)
Teams em outro idioma não casa nada: sem sugestão, o fluxo de sempre.

Privacidade: os nomes ficam só no meta.json local da gravação; o scriba.log recebe
contagens, nunca nomes (o log vai anexado no "Reportar erro").
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time

log = logging.getLogger("scriba.teamsroster")

SAMPLE_SECONDS = 5.0
# Mic aberto por menos que isso não conta (abriu por engano, fechou em seguida).
MIN_UNMUTED_SECONDS = 10.0
# Gap entre amostras acima disso (máquina suspensa, Teams travado) não vira
# "tempo de mic aberto": conta no máximo um intervalo normal (com folga p/ a leitura).
_MAX_STEP = 1.5 * SAMPLE_SECONDS

_TILE_SUFFIX = ", Context menu is available"
_ROSTER_STATE = re.compile(r", (Muted|Unmuted)$")
_SPEAKING = re.compile(r"^(?P<name>.+) is speaking$")
_TOTAL = re.compile(r"^In this meeting, (\d+) total")
_PROFILE = re.compile(r"^Profile picture of (?P<name>.+?)\.?$")
_EXTERNAL = re.compile(r"\s+External(?: unfamiliar)?$")
_BOT = re.compile(r"notetaker|\bbot\b", re.IGNORECASE)
_RANK = {"muted": 0, "open": 1, "speaking": 2}

# Onde os sinais moram na janela da call (ControlType do UI Automation).
_CONTROL_TYPES = (50011, 50024, 50026)  # MenuItem, TreeItem, Group


def _person(raw: str) -> str:
    """Nome da pessoa no começo do texto: antes da 1ª vírgula, sem o selo de externo."""
    return _EXTERNAL.sub("", raw.split(",", 1)[0]).strip()


def parse(text: str) -> tuple[str, str] | None:
    """(nome, sinal) de um Name da janela da call; sinal = "muted" | "open" |
    "speaking". None = texto que não fala de participante."""
    text = (text or "").strip()
    m = _SPEAKING.match(text)
    if m:
        name = m.group("name").strip()
        return None if name.casefold() == "nobody" else (name, "speaking")
    if text.endswith(_TILE_SUFFIX):
        if text.startswith("Content shared by "):
            return None
        flags = [f.strip() for f in text[: -len(_TILE_SUFFIX)].split(",")[1:]]
        return _person(text), "muted" if "muted" in flags else "open"
    m = _ROSTER_STATE.search(text)
    if m:
        return _person(text), "muted" if m.group(1) == "Muted" else "open"
    return None


def parse_total(text: str) -> int | None:
    """Total do cabeçalho do painel People ("In this meeting, 56 total")."""
    m = _TOTAL.match((text or "").strip())
    return int(m.group(1)) if m else None


def parse_profile(text: str) -> str | None:
    """Nome do dono da conta, pelo avatar do perfil."""
    m = _PROFILE.match((text or "").strip())
    return m.group("name").strip() if m else None


class RosterTracker:
    """Acumula, amostra a amostra, quem ficou com o mic aberto e quem falou."""

    def __init__(self) -> None:
        self.self_name: str | None = None
        self.total: int | None = None
        self.samples = 0
        self._order: list[str] = []          # ordem em que cada um abriu o mic
        self._open_s: dict[str, float] = {}
        self._speaking: set[str] = set()
        self._last: float | None = None

    def feed(self, texts: list[str], now: float) -> None:
        step = 0.0 if self._last is None else min(max(now - self._last, 0.0), _MAX_STEP)
        self._last = now
        state: dict[str, str] = {}
        for t in texts:
            total = parse_total(t)
            if total is not None:
                self.total = max(self.total or 0, total)
                continue
            p = parse(t)
            if p is None or _BOT.search(p[0]):
                continue
            name, sinal = p
            # a mesma pessoa aparece no bloco de vídeo e no painel: falar > aberto > mudo
            if _RANK[sinal] > _RANK.get(state.get(name, ""), -1):
                state[name] = sinal
        if not state:
            # janela ainda sem árvore (1ª leitura do Chromium) ou Teams em outro
            # idioma: nada reconhecido não é amostra - "ninguém falou" exige ver gente
            return
        self.samples += 1
        for name, sinal in state.items():
            if sinal == "muted":
                continue
            if name not in self._open_s:
                self._open_s[name] = 0.0
                self._order.append(name)
            self._open_s[name] += step
            if sinal == "speaking":
                self._speaking.add(name)

    def speakers(self) -> list[str]:
        """Quem falou: apareceu falando ou ficou com o mic aberto por tempo suficiente.
        O dono da conta (a voz do mic, fora do loopback) não entra."""
        me = (self.self_name or "").casefold()
        return [n for n in self._order
                if n.casefold() != me
                and (n in self._speaking or self._open_s[n] >= MIN_UNMUTED_SECONDS)]


class RosterWatch:
    """Amostra a janela da call do Teams numa thread própria até `stop()`.

    `stop()` só sinaliza, nunca espera: UI Automation cruza processo, e um Teams
    travado não pode segurar o fim da gravação. O resultado é o que já foi
    acumulado até ali.
    """

    def __init__(self, sample_seconds: float = SAMPLE_SECONDS) -> None:
        self.tracker = RosterTracker()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sample_seconds = sample_seconds

    def start(self) -> "RosterWatch":
        if sys.platform == "win32":
            threading.Thread(target=self._run, daemon=True, name="teams-roster").start()
        return self

    def stop(self) -> dict | None:
        """Encerra e devolve {"speakers": [...], "total": N} - None sem dado útil."""
        self._stop.set()
        with self._lock:
            t = self.tracker
            if not t.samples:
                return None
            speakers = t.speakers()
            log.info("Teams: %d amostra(s) da call, %d pessoa(s) abriram o mic, total %s",
                     t.samples, len(speakers), t.total if t.total is not None else "?")
            out = {"speakers": speakers}
            if t.total is not None:
                out["total"] = t.total
            return out

    def _run(self) -> None:
        from . import uia_win, wintitles
        from .detector import _is_teams_call_window

        while not self._stop.is_set():
            try:
                self._sample(uia_win, wintitles, _is_teams_call_window)
            except Exception:
                log.debug("amostra da janela do Teams falhou", exc_info=True)
            self._stop.wait(self._sample_seconds)

    def _sample(self, uia_win, wintitles, is_call_window) -> None:
        wins = wintitles.windows({"ms-teams.exe"})
        calls = [h for h, t in wins if is_call_window(t)]
        if not calls:
            return
        if self.tracker.self_name is None:
            for h, t in wins:
                if h in calls:
                    continue
                for n in uia_win.names_under(h, "idna-me-control-avatar-trigger", self._stop):
                    name = parse_profile(n)
                    if name:
                        with self._lock:
                            self.tracker.self_name = name
                        break
        texts: list[str] = []
        for h in calls:
            texts += uia_win.descendant_names(h, set(_CONTROL_TYPES), self._stop)
        if self._stop.is_set():
            return
        with self._lock:
            self.tracker.feed(texts, time.monotonic())


def debug_loop() -> int:
    """`scribadev teams`: mostra ao vivo quem abriu o mic na call do Teams (teste manual).

    Imprime no console (nomes inclusive) - o scriba.log não recebe nada disso."""
    if sys.platform != "win32":
        print("leitura da janela do Teams só existe no Windows")
        return 1
    watch = RosterWatch().start()
    print(f"Lendo a janela da call do Teams a cada {SAMPLE_SECONDS:.0f}s. Ctrl+C para sair.")
    last = None
    try:
        while True:
            time.sleep(SAMPLE_SECONDS)
            with watch._lock:
                t = watch.tracker
                cur = (t.samples, t.self_name, t.total, tuple(t.speakers()))
            if cur[0] == 0:
                print(f"[{time.strftime('%H:%M:%S')}] nenhuma janela de call reconhecida ainda")
                continue
            if cur[1:] != (last or (None,))[1:]:
                print(f"[{time.strftime('%H:%M:%S')}] você: {cur[1] or '?'} | total: {cur[2] or '?'} | "
                      f"abriram o mic ({len(cur[3])}): {', '.join(cur[3]) or '-'}")
            last = cur
    except KeyboardInterrupt:
        result = watch.stop()
        print(f"sugestão para o fim da call: {len(result['speakers']) if result else 'nenhuma'}")
    return 0
