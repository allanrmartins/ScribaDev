"""Diagnóstico do ScribaDev: lê o log do app, monta um relatório de ambiente e
empacota tudo num .zip para o usuário enviar ao desenvolvedor (suporte).

Sem tkinter — lógica pura e testável. A UI (log_ui.LogWindow) só chama estas funções.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from . import util

log = logging.getLogger("scriba.diagnostics")

LOG_FILE = util.LOGS_DIR / "scriba.log"
_SECRET_KEYS = ("hf_token", "api_key", "cloud_api_key")


def read_tail(path, n: int = 800) -> str:
    """Últimas `n` linhas do arquivo (texto), tolerante a ausência/erro."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(sem log ainda — aparece após a primeira execução)"
    return "\n".join(lines[-n:]) if lines else "(log vazio)"


LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
# cabeçalho de uma linha de log: "DD/MM/AAAA HH:MM:SS NÍVEL ..." (formato do _setup_logging)
_ENTRY_RE = re.compile(r"^(\d{2}/\d{2}/\d{4}) (\d{2}:\d{2}:\d{2}) (\w+) ")


def parse_entries(text: str) -> list:
    """Quebra o log em entradas. Cada entrada começa numa linha com cabeçalho e
    INCORPORA as linhas seguintes sem cabeçalho (o traceback de um erro). Devolve
    [{date, time, level, text}] — assim um crash vira UMA entrada destacável."""
    entries: list = []
    for line in str(text).splitlines():
        m = _ENTRY_RE.match(line)
        if m:
            entries.append({"date": m.group(1), "time": m.group(2),
                            "level": m.group(3).upper(), "text": line})
        elif entries:
            entries[-1]["text"] += "\n" + line  # continuação (traceback) da entrada acima
        else:
            entries.append({"date": "", "time": "", "level": "", "text": line})
    return entries


def level_num(level: str) -> int:
    return LEVELS.get(str(level).upper(), 20)  # nível desconhecido ~ INFO


def filter_entries(entries, level_min: int = 0, date_br: str = "", time_from: str = "") -> list:
    """Filtra por nível mínimo, dia (DD/MM/AAAA exato) e hora a partir de (HH:MM)."""
    out = []
    for e in entries:
        if level_num(e["level"]) < level_min:
            continue
        if date_br and e.get("date") != date_br:
            continue
        if time_from and (e.get("time") or "")[:5] < time_from:
            continue
        out.append(e)
    return out


def redact_config(text: str) -> str:
    """Esconde as chaves secretas (hf_token / api_key / cloud_api_key). Mesmo cifradas
    (DPAPI, atreladas à máquina), não devem sair daqui."""
    out = []
    for line in str(text).splitlines():
        if any(line.lstrip().startswith(k) for k in _SECRET_KEYS):
            out.append(line.split("=", 1)[0] + '= "***"  # redigido no diagnóstico')
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if text else "")


def environment_report() -> str:
    """Resumo do ambiente (versão, Python, SO, GPU, diarização) — o que o suporte precisa."""
    from . import __version__, updates

    # via camada de plataforma: ctypes.WinDLL nem existe fora do Windows e o
    # except OSError antigo não pegaria o AttributeError no Linux/macOS (#98)
    from . import plat

    gpu = "sim (NVIDIA)" if plat.has_nvidia_gpu() else "nao"
    try:
        import importlib.util as ilu

        diar = "instalada" if ilu.find_spec("pyannote.audio") else "ausente"
    except Exception:
        diar = "erro ao checar"
    return "\n".join([
        f"ScribaDev versao : {__version__}",
        f"Instalacao git   : {updates.is_git_install()}",
        f"Instalador (exe) : {updates.is_frozen_install()}",
        f"Python           : {sys.version.split()[0]}",
        f"SO               : {platform.platform()}",
        f"GPU NVIDIA       : {gpu}",
        f"pyannote.audio   : {diar}",
        f"APP_DIR          : {util.APP_DIR}",
        f"Gerado em        : {datetime.now().isoformat(timespec='seconds')}",
    ]) + "\n"


GH_NEW_ISSUE = "https://github.com/allanrmartins/ScribaDev/issues/new"


def bug_report_url(tail_lines: int = 50, max_url: int = 7500) -> str:
    """URL do GitHub 'new issue' com título e corpo já preenchidos (ambiente + as
    últimas linhas do log). O usuário revisa no navegador antes de postar — o corpo é
    montado 100% local e nada é enviado por aqui. Como o repo é privado, a issue só é
    criada se ele estiver logado no GitHub. Trunca o tail (mantendo as linhas mais
    recentes) até a URL caber no limite de querystring do GitHub (~8 KB)."""
    import urllib.parse

    env = environment_report().rstrip()
    lines = read_tail(LOG_FILE, n=2000).splitlines()
    tail = lines[-tail_lines:] if tail_lines > 0 else []

    def make(tail_list: list) -> str:
        body = (
            "## O que aconteceu\n\n_(descreva o problema aqui)_\n\n"
            "## Passos para reproduzir\n\n1. \n2. \n\n"
            f"## Ambiente\n\n```\n{env}\n```\n\n"
            f"## Log (últimas {len(tail_list)} linhas)\n\n```\n" + "\n".join(tail_list) + "\n```\n"
        )
        return GH_NEW_ISSUE + "?" + urllib.parse.urlencode({"title": "Bug: ", "body": body})

    url = make(tail)
    while len(url) > max_url and len(tail) > 1:
        tail = tail[len(tail) // 4 + 1:]  # descarta ~25% das linhas mais antigas, mantém o fim
        url = make(tail)
    return url


def latest_failed(rec_dir) -> Path | None:
    """Pasta da reunião com falha (status failed/no_audio) mais RECENTE, ou None."""
    best, best_mtime = None, -1.0
    try:
        for meta_path in Path(rec_dir).rglob("meta.json"):
            try:
                m = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if m.get("status") in ("failed", "no_audio"):
                mt = meta_path.stat().st_mtime
                if mt > best_mtime:
                    best, best_mtime = meta_path.parent, mt
    except OSError:
        pass
    return best


def export_zip(recordings_dir=None) -> Path:
    """Empacota log(s) + ambiente + config (sem segredos) + a última reunião com falha
    num .zip no Desktop. Devolve o caminho do .zip."""
    util.ensure_app_dirs()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    desktop = Path.home() / "Desktop"
    dest = (desktop if desktop.is_dir() else Path.home()) / f"scribadev-diagnostico-{stamp}.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        # 1) TODOS os logs do app: scriba.log (+ rotacionados), pip.log (downloads de
        # componentes — sem ele o "pip retornou N" chega sem o traceback), hang.log,
        # relaunch.log
        for p in sorted(util.LOGS_DIR.glob("*.log*")):
            try:
                z.write(p, f"logs/{p.name}")
            except OSError:
                pass
        # 2) ambiente
        z.writestr("ambiente.txt", environment_report())
        # 3) config sem segredos
        try:
            z.writestr("config.redacted.toml", redact_config(util.CONFIG_PATH.read_text(encoding="utf-8")))
        except OSError:
            pass
        # 4) a reunião que falhou mais recente (process.log + meta.json) — o caso de suporte
        if recordings_dir:
            folder = latest_failed(recordings_dir)
            if folder:
                for fn in ("process.log", "meta.json"):
                    p = folder / fn
                    if p.exists():
                        try:
                            z.write(p, f"reuniao_com_falha/{fn}")
                        except OSError:
                            pass
    return dest
