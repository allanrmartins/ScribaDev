"""Backend POSIX (Linux + macOS) da camada de plataforma (#104).

Quando Linux e macOS divergirem de verdade (captura, notificações nativas),
este módulo se divide em _linux.py/_mac.py; por ora os ramos por SO cabem aqui.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("scriba.plat")


def app_data_dir() -> Path:
    """Diretório de dados do app na convenção de cada SO.

    Linux: $XDG_DATA_HOME/ScribaDev (fallback ~/.local/share/ScribaDev).
    macOS: ~/Library/Application Support/ScribaDev.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ScribaDev"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "ScribaDev"


def default_recordings_dir() -> Path:
    """Fora do Windows não existe C:\\temp — as gravações ficam sob o app_data_dir."""
    return app_data_dir() / "gravacoes"


def has_nvidia_gpu() -> bool:
    """Driver NVIDIA presente? Linux: libcuda.so.1 carrega; macOS: não existe CUDA."""
    if sys.platform == "darwin":
        return False
    import ctypes

    try:
        ctypes.CDLL("libcuda.so.1")
        return True
    except OSError:
        return False


# ------------------------------------------------------- PATH do usuário ----
# App lançado do Finder/Dock/LaunchAgent NÃO herda o PATH do shell: o launchd
# entrega `/usr/bin:/bin:/usr/sbin:/sbin` e nada mais. Resultado no app instalado
# (e só nele — do terminal tudo funciona): `shutil.which` não acha o `claude` nem
# o `ffmpeg`, e a UI/doctor dizem que não estão instalados.

# Onde ferramentas de usuário costumam morar. Só entram no PATH se existirem.
_DIRS_EXTRA = (
    "~/.local/bin",       # instalador nativo do Claude Code, pipx, uv
    "~/.claude/local",    # instalação "local" (antiga) do Claude Code
    "~/bin",
    "/opt/homebrew/bin",  # Homebrew no Apple Silicon — onde o ffmpeg do mac mora
    "/opt/homebrew/sbin",
    "/usr/local/bin",     # Homebrew no Intel + instaladores diversos
    "/usr/local/sbin",
    "/opt/local/bin",     # MacPorts
    "/opt/local/sbin",
    "/snap/bin",          # Linux
)

# PATH que o launchd dá a app de GUI: se o nosso é subconjunto disto, fomos
# lançados por ele (não por um shell) e vale perguntar o PATH real ao shell.
_PATH_DO_LAUNCHD = frozenset(("/usr/bin", "/bin", "/usr/sbin", "/sbin"))

_MARCA = ("__scriba_path_ini__", "__scriba_path_fim__")


def _path_do_shell(timeout: float = 5.0) -> list[str]:
    """PATH do shell de login+interativo do usuário, ou [] se não der.

    `-l -i` de propósito: muita gente monta o PATH no ~/.zshrc, que um shell
    SÓ-login não lê. Os marcadores isolam o valor do ruído que rc com plugins
    costuma imprimir. Qualquer falha (rc quebrado, shell inexistente, timeout)
    devolve [] — os _DIRS_EXTRA cobrem o caso comum.
    """
    import re
    import subprocess

    shell = os.environ.get("SHELL") or "/bin/sh"
    ini, fim = _MARCA
    try:
        out = subprocess.run(
            [shell, "-l", "-i", "-c", f'printf "{ini}%s{fim}" "$PATH"'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
        ).stdout or ""
    except (OSError, subprocess.SubprocessError, ValueError):
        log.debug("não deu para perguntar o PATH ao shell", exc_info=True)
        return []
    m = re.search(f"{re.escape(ini)}(.*?){re.escape(fim)}", out, re.S)
    return [d for d in m.group(1).split(os.pathsep) if d] if m else []


def ensure_user_path() -> str:
    """Completa o PATH do processo com os diretórios de ferramentas do usuário.

    Só ACRESCENTA no fim: a ordem do que já estava no PATH é preservada, então
    rodando da CLI (PATH do shell) isto não muda qual binário é escolhido. O
    shell só é consultado quando o PATH é o do launchd — na CLI nem spawna.

    Devolve o PATH final (para log/diagnóstico).
    """
    atual = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    vistos = set(atual)
    candidatos = []
    if all(d in _PATH_DO_LAUNCHD for d in atual):
        candidatos += _path_do_shell()
    candidatos += [str(Path(d).expanduser()) for d in _DIRS_EXTRA]
    for d in candidatos:
        if d not in vistos and Path(d).is_dir():
            atual.append(d)
            vistos.add(d)
    os.environ["PATH"] = os.pathsep.join(atual)
    return os.environ["PATH"]


def open_path(path) -> None:
    """Abre arquivo ou pasta no gerenciador do SO (xdg-open no Linux, open no macOS)."""
    import subprocess

    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(path)])


# ------------------------------------------------- instância única (#100) ---
# Análogo POSIX do mutex nomeado do Windows: flock num lockfile. O flock morre
# junto com o processo (inclusive via os._exit), então o retry pós-update (#74)
# funciona igual: a nova instância espera a antiga sair e adquire o lock.

_lock_file = None  # mantém o arquivo (e o flock) vivos durante o processo


def _lock_path() -> Path:
    return app_data_dir() / "instance.lock"


def _sock_path() -> Path:
    return app_data_dir() / "show.sock"


def single_instance(retries: int = 0, delay: float = 0.4, name: str | None = None) -> bool:
    """True se esta é a única instância (adquiriu o flock do lockfile).

    `name` sobrepõe o caminho do lockfile (testes). Mesmo contrato do backend
    Windows: com `retries` > 0, fecha e reespera `delay` s a cada tentativa.
    """
    import fcntl
    import time

    global _lock_file
    path = Path(name) if name else _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries + 1):
        f = open(path, "w", encoding="utf-8")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            f.write(str(os.getpid()))
            f.flush()
            _lock_file = f
            return True
        except OSError:
            f.close()
            if attempt < retries:
                time.sleep(delay)
    return False


def signal_show_window() -> bool:
    """Acorda a instância que já está rodando (True se conseguiu sinalizar)."""
    import socket

    s = socket.socket(socket.AF_UNIX)
    try:
        s.connect(str(_sock_path()))
        return True
    except OSError:
        return False
    finally:
        s.close()


def show_window_listener(stop_event, on_signal) -> None:
    """Loop (para uma daemon-thread): espera o sinal de uma 2ª instância e chama on_signal."""
    import socket

    sp = _sock_path()
    try:
        sp.unlink(missing_ok=True)  # sock órfão de um crash: só o dono do lock escuta
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(str(sp))
        srv.listen(1)
        srv.settimeout(1.0)  # reavalia o stop_event a cada 1 s (igual ao backend win)
    except OSError:
        log.warning("não consegui criar o socket de ativação em %s", sp)
        return
    with srv:
        while not stop_event.is_set():
            try:
                conn, _ = srv.accept()
                conn.close()
                on_signal()
            except TimeoutError:
                continue
            except OSError:
                return


def spawn_relaunch(pid: int, log_path) -> None:
    """Sobe uma nova instância detached; ela espera esta sair via retry no lock.

    Diferente do Windows (que precisa do PowerShell Wait-Process), aqui não há
    intermediário: o flock solta sozinho quando este processo morre, e a nova
    instância (SCRIBA_RELAUNCHED=1) dá retry no single_instance até adquirir.
    Não usa os.execv de propósito: execv substituiria o processo NA HORA,
    abandonando o teardown gracioso do Qt (uma gravação sendo salva na saída
    seria cortada). Exceções propagam: o chamador decide como avisar.
    """
    import subprocess
    import time

    env = dict(os.environ, SCRIBA_RELAUNCHED="1")
    logf = open(log_path, "a", encoding="utf-8")
    logf.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] relaunch: subindo nova instancia (aguarda pid {pid} soltar o lock)\n")
    logf.flush()
    subprocess.Popen(
        [sys.executable, "-m", "scriba.cli", "run"],
        stdout=logf, stderr=subprocess.STDOUT,
        start_new_session=True,  # sobrevive à morte deste processo (setsid)
        env=env,
    )
