"""Login do claude CLI, o provider padrão do resumo.

Com a CLI deslogada, toda reunião sai sem resumo e o usuário só descobre ao abrir
a nota. O app checa o login uma vez por dia (e a cada tick enquanto estiver
deslogado), mostra um banner vermelho na capa e repete um toast de tempos em
tempos. Só vale para quem tem o resumo ligado com o provider claude.

Duas fontes, porque nenhuma basta sozinha:
- `claude auth status --json`: barato (~200 ms, zero tokens), mas olha a
  credencial salva. Uma sessão OAuth vencida que não renova pode seguir
  aparecendo como `loggedIn: true` (2026-10-05).
- o marcador `claude_logged_out_at` no state.json, gravado por `ai._claude_cli`
  quando uma chamada real bate em logout. O resumo roda num subprocesso, então
  é por aqui que o app principal fica sabendo.
Com o marcador presente (ou na checagem manual), vale uma chamada real
(`ai.test_connection`), que é a prova definitiva.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import datetime

from . import util

log = logging.getLogger("scriba.claude_auth")

MARK_KEY = "claude_logged_out_at"     # ISO: uma chamada real bateu em logout
DAILY_KEY = "claude_auth_checked"     # data ISO da última checagem conclusiva
TOAST_KEY = "claude_auth_toast_at"    # epoch do último toast de deslogado

TICK_S = 30 * 60           # intervalo do loop do app
TOAST_EVERY_S = 2 * 3600   # repetição do toast enquanto seguir deslogado
_STATUS_TIMEOUT_S = 20

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def applies(cfg) -> bool:
    """O resumo está ligado e usa o claude CLI? (Ollama/OpenAI não têm login.)"""
    s = getattr(cfg, "summary", None)
    if s is None or not getattr(s, "enabled", False):
        return False
    return (getattr(s, "provider", "") or "claude").strip().lower() == "claude"


def status() -> bool | None:
    """`claude auth status --json`: True logado, False deslogado, None sem como
    saber (CLI ausente, travou, saída ilegível). Não gasta tokens."""
    cmd = util.claude_command()
    if cmd is None:
        return None
    try:
        proc = subprocess.run(
            cmd + ["auth", "status", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_STATUS_TIMEOUT_S, creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.info("claude auth status falhou (%s)", e)
        return None
    # o exit code é 1 quando deslogado: decide pelo JSON, não pelo rc
    out = proc.stdout or ""
    start, end = out.find("{"), out.rfind("}")
    if start < 0 or end < start:
        log.info("claude auth status sem JSON (rc=%s)", proc.returncode)
        return None
    try:
        data = json.loads(out[start:end + 1])
    except ValueError:
        return None
    logged = data.get("loggedIn")
    return logged if isinstance(logged, bool) else None


def probe() -> bool | None:
    """Chamada real e mínima ao modelo: True respondeu, False deslogado, None
    falhou por outro motivo (rede, timeout). Gasta uma requisição curta."""
    from . import ai

    if util.claude_command() is None:
        return None
    ai.last_error = None
    ok, _msg = ai.test_connection()
    if ok:
        return True
    return False if ai.last_error == ai.ERR_LOGGED_OUT else None


def marked() -> bool:
    return bool(util.read_state().get(MARK_KEY))


def mark_logged_out() -> None:
    """Chamada real bateu em logout (resumo, chat ou wizard, em qualquer processo)."""
    util.update_state(**{MARK_KEY: datetime.now().isoformat(timespec="seconds")})


def clear_mark() -> None:
    if marked():
        util.update_state(**{MARK_KEY: None})


def check(*, force: bool = False, real: bool = False) -> bool | None:
    """Decide o login agora. `force` ignora o gate diário; `real` exige a chamada
    real (checagem manual). Devolve None quando não checou ou não deu para saber."""
    st = util.read_state()
    has_mark = bool(st.get(MARK_KEY))
    today = datetime.now().date().isoformat()
    if not (force or real or has_mark or st.get(DAILY_KEY) != today):
        return None
    logged = status()
    if logged is not False and (real or has_mark):
        # status pode mentir com a sessão OAuth vencida: tira a prova
        logged = probe()
    if logged is not None:
        util.update_state(**{DAILY_KEY: today})
    if logged is True:
        clear_mark()
    return logged


def should_toast(now: float | None = None) -> bool:
    """Já passou TOAST_EVERY_S desde o último toast? (sobrevive a reinícios)"""
    now = time.time() if now is None else now
    last = util.read_state().get(TOAST_KEY)
    if not last:
        return True   # nunca avisou (ou rearmado após o login)
    try:
        return now - float(last) >= TOAST_EVERY_S
    except (TypeError, ValueError):
        return True


def toasted(now: float | None = None) -> None:
    util.update_state(**{TOAST_KEY: time.time() if now is None else now})


def reset_toast() -> None:
    util.update_state(**{TOAST_KEY: 0})
