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
