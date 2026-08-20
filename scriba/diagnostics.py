"""Diagnóstico do ScribaDev: lê o log do app, monta um relatório de ambiente e
empacota tudo num .zip para o usuário enviar ao desenvolvedor (suporte).

Privacidade: TUDO que sai daqui (logs, ambiente, config, meta da reunião com
falha) passa pelo Scrubber antes de ser empacotado - nomes de cliente, pessoas
e títulos de reunião viram tokens estáveis; segredos já eram redigidos. O que
o suporte precisa (fluxo, erros, timing, versões) fica intacto.

Sem tkinter — lógica pura e testável. A UI (log_ui.LogWindow) só chama estas funções.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import sqlite3
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


def error_tail(text: str, n: int = 3, max_chars: int = 900) -> str:
    """Últimas `n` entradas de nível ERROR+ do log (cada uma já com o traceback
    incorporado por parse_entries), p/ o corpo de um reporte de issue. Sem nenhum
    erro no recorte, cai nas últimas entradas quaisquer (contexto ainda ajuda)."""
    entries = parse_entries(text)
    errs = [e for e in entries if level_num(e["level"]) >= level_num("ERROR")]
    pick = (errs or entries)[-n:]
    return "\n".join(e["text"] for e in pick)[-max_chars:]


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


# -- ofuscação de dados sensíveis ---------------------------------------------

_MIN_VOCAB_LEN = 3   # valor curto demais ("PX") vira falso positivo com IGNORECASE

# Linhas cujo conteúdo livre NÃO vem de base local nenhuma (título de janela ao
# vivo; nomes recém-diarizados de reunião que falhou antes de indexar): o que
# vem depois do prefixo é redigido em bloco.
_LINE_RULES = (
    (re.compile(r"(possível nova call sem ciclo de mic:).*"), r"\1 [janela]"),
    (re.compile(r"(vozes reconhecidas:).*"), r"\1 [pessoas]"),
)
# usuário do SO em caminhos (C:\Users\Fulano\..., /home/fulano/...)
_USER_PATH_RE = re.compile(r"(?i)(?<=[/\\]users[/\\])[^/\\\s]+|(?<=/home/)[^/\s]+")

# chaves do meta.json cujo valor é sensível (alimentam o vocabulário na hora
# de empacotar a reunião com falha - que pode nem estar indexada ainda)
_META_VOCAB = (("client", "cliente"), ("title", "reuniao"),
               ("meeting_title", "reuniao"), ("teams_title", "reuniao"),
               ("window_title", "reuniao"))

_OFUSCACAO_NOTE = """\
Dados pessoais foram OFUSCADOS automaticamente antes do empacotamento:
- [cliente-N] / [pessoa-N] / [reuniao-N]: nomes de clientes, pessoas e reuniões.
  O MESMO número é sempre o MESMO valor - dá para correlacionar sem saber o nome.
- [usuario]: o usuário do Windows/SO (aparece em caminhos).
- [janela] / [pessoas]: trechos redigidos em bloco (título de janela ao vivo,
  mapeamento de vozes da diarização).
Nada disso faz diferença para a análise técnica do log; o restante (fluxo,
erros, tracebacks, timing, versões) está intacto.
"""


class Scrubber:
    """Ofusca nomes em textos de diagnóstico com tokens ESTÁVEIS por valor
    ([cliente-1], [pessoa-2]...) - o log continua correlacionável pelo suporte.

    O vocabulário vem das bases LOCAIS (índice de reuniões, banco do timesheet,
    pastas de gravação), abertas READ-ONLY e toleradas ausentes - nunca cria
    arquivo (dormência). Best-effort declarado: nome que não está em base
    nenhuma é coberto pelas regras de linha (_LINE_RULES), não pelo vocabulário.
    """

    def __init__(self):
        self._tokens: dict[str, str] = {}    # casefold(valor) -> token
        self._values: list[str] = []
        self._counts: dict[str, int] = {}
        self._regex = None

    def add(self, value, kind: str) -> None:
        """Registra um valor sensível; curto demais ou repetido (sem caixa) é no-op."""
        text = " ".join(str(value or "").split())
        key = text.casefold()
        if len(text) < _MIN_VOCAB_LEN or key in self._tokens:
            return
        if kind == "usuario":
            self._tokens[key] = "[usuario]"
        else:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            self._tokens[key] = f"[{kind}-{self._counts[kind]}]"
        self._values.append(text)
        self._regex = None

    def load_local_data(self, recordings_dir=None) -> None:
        """Vocabulário das bases locais. Tudo read-only e best-effort: base
        ausente ou corrompida é pulada (o diagnóstico nunca pode falhar por
        causa da ofuscação)."""
        try:
            import getpass

            self.add(getpass.getuser(), "usuario")
        except Exception:
            pass
        self._load_db(util.APP_DIR / "index.db", (
            ("SELECT DISTINCT name FROM participants", "pessoa", False),
            ("SELECT DISTINCT client FROM meetings", "cliente", False),
            ("SELECT DISTINCT title FROM meetings", "reuniao", False),
            ("SELECT DISTINCT meeting_title FROM meetings", "reuniao", False),
            ("SELECT DISTINCT folder FROM meetings", "reuniao", True),
            ("SELECT DISTINCT export_path FROM meetings", "reuniao", True),
        ))
        from . import timesheet_db  # importar não cria nada (dormência, #126)

        self._load_db(timesheet_db._db_path(), (
            ("SELECT name FROM clients", "cliente", False),
            ("SELECT alias FROM client_aliases", "cliente", False),
            ("SELECT DISTINCT client_text FROM entries WHERE client_text != ''",
             "cliente", False),
        ))
        if recordings_dir:
            try:
                for meta_path in Path(recordings_dir).rglob("meta.json"):
                    name = meta_path.parent.name
                    if re.search(r"[^\W\d_]", name):   # só pasta RENOMEADA (tem letra)
                        self.add(name, "reuniao")
            except OSError:
                pass

    def add_meta(self, meta: dict) -> None:
        """Nomes do meta.json da reunião com falha (pode nem estar indexada)."""
        for key, kind in _META_VOCAB:
            self.add(meta.get(key), kind)
        for p in meta.get("participants") or ():
            self.add(p.get("name") if isinstance(p, dict) else p, "pessoa")

    def _load_db(self, path, queries) -> None:
        path = Path(path)
        if not path.exists():
            return
        try:
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return
        try:
            for sql, kind, basename in queries:
                try:
                    for (v,) in conn.execute(sql):
                        self.add(Path(str(v)).name if basename and v else v, kind)
                except sqlite3.Error:
                    continue
        finally:
            conn.close()

    def _compiled(self):
        if self._regex is None and self._values:
            alt = "|".join(re.escape(v) for v in
                           sorted(self._values, key=len, reverse=True))
            self._regex = re.compile(rf"(?<!\w)(?:{alt})(?!\w)", re.IGNORECASE)
        return self._regex

    def scrub(self, text) -> str:
        out = str(text)
        rx = self._compiled()
        if rx is not None:
            out = rx.sub(lambda m: self._tokens.get(m.group().casefold(), "[dado]"), out)
        out = _USER_PATH_RE.sub("[usuario]", out)
        for line_rx, repl in _LINE_RULES:
            out = line_rx.sub(repl, out)
        return out


def build_scrubber(recordings_dir=None) -> Scrubber:
    """Scrubber carregado com as bases locais; falha na carga NUNCA sobe (o
    reporte tem que sair) - sai um scrubber parcial e o problema vai pro log."""
    s = Scrubber()
    try:
        s.load_local_data(recordings_dir)
    except Exception:
        log.exception("diagnóstico: carga do vocabulário de ofuscação falhou (sigo parcial)")
    return s


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
    últimas linhas do log, OFUSCADAS pelo Scrubber). O usuário revisa no navegador
    antes de postar — o corpo é montado 100% local e nada é enviado por aqui.
    Trunca o tail (mantendo as linhas mais recentes) até a URL caber no limite de
    querystring do GitHub (~8 KB)."""
    import urllib.parse

    scrubber = build_scrubber()
    env = scrubber.scrub(environment_report()).rstrip()
    lines = scrubber.scrub(read_tail(LOG_FILE, n=2000)).splitlines()
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
    num .zip no Desktop. Devolve o caminho do .zip.

    Tudo passa pelo Scrubber antes de entrar no zip: nome de cliente, pessoa e
    reunião vira token estável, usuário do SO vira [usuario] - nada disso faz
    diferença na análise; o resto (fluxo, erros, timing) fica intacto."""
    util.ensure_app_dirs()
    scrubber = build_scrubber(recordings_dir)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    desktop = Path.home() / "Desktop"
    dest = (desktop if desktop.is_dir() else Path.home()) / f"scribadev-diagnostico-{stamp}.zip"

    # a reunião com falha vem ANTES dos logs: o meta.json dela alimenta o
    # vocabulário (nomes que talvez nem estejam indexados ainda e que também
    # aparecem no scriba.log)
    failed: list[tuple[str, str]] = []
    folder = latest_failed(recordings_dir) if recordings_dir else None
    if folder:
        scrubber.add(folder.name, "reuniao")
        for fn in ("meta.json", "process.log"):
            try:
                text = (folder / fn).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if fn == "meta.json":
                try:
                    scrubber.add_meta(json.loads(text))
                except ValueError:
                    pass
            failed.append((fn, text))

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        # 1) TODOS os logs do app: scriba.log (+ rotacionados), pip.log (downloads de
        # componentes — sem ele o "pip retornou N" chega sem o traceback), hang.log,
        # relaunch.log
        for p in sorted(util.LOGS_DIR.glob("*.log*")):
            try:
                z.writestr(f"logs/{p.name}",
                           scrubber.scrub(p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
        # 2) ambiente (APP_DIR carrega o usuário do SO)
        z.writestr("ambiente.txt", scrubber.scrub(environment_report()))
        # 3) config sem segredos (e sem default_client/caminhos com nomes)
        try:
            z.writestr("config.redacted.toml",
                       scrubber.scrub(redact_config(util.CONFIG_PATH.read_text(encoding="utf-8"))))
        except OSError:
            pass
        # 4) a reunião que falhou mais recente (meta.json + process.log) — o caso de suporte
        for fn, text in failed:
            z.writestr(f"reuniao_com_falha/{fn}", scrubber.scrub(text))
        z.writestr("OFUSCACAO.txt", _OFUSCACAO_NOTE)
    return dest
