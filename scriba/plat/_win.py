"""Backend Windows da camada de plataforma — comportamento idêntico ao histórico.

Código MOVIDO de util.py/config.py/main.py (não reescrito): qualquer mudança
aqui é mudança de comportamento no Windows e precisa de justificativa própria (#104).
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from pathlib import Path

log = logging.getLogger("scriba.plat")


def app_data_dir() -> Path:
    """%LOCALAPPDATA%\\ScribaDev (fallback: home) — o APP_DIR de sempre."""
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ScribaDev"


def default_recordings_dir() -> Path:
    """Default histórico das gravações (config [output] recordings_dir vazio)."""
    return Path(r"C:\temp\scribadev\gravacoes")


# STILL_ACTIVE do Win32: GetExitCodeProcess devolve isto enquanto o processo vive
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def pid_alive(pid: int) -> bool:
    """O processo `pid` ainda existe? (dono dos .lock e do marcador .installing)

    NÃO usar `os.kill(pid, 0)` aqui: no Windows `signal.CTRL_C_EVENT == 0`, então
    aquilo NÃO é uma sondagem — o Python chama `GenerateConsoleCtrlEvent`, que
    dispara Ctrl+C no grupo de console (derrubou a suíte no CI com KeyboardInterrupt)
    e, num app sem console, falha sempre, fazendo todo PID vivo parecer morto.
    Qualquer outro sinal seria pior: o Python cai no `TerminateProcess` e MATA o
    processo. A sondagem correta é abrir um handle só de consulta.
    """
    if pid <= 0:
        return False
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False        # não existe (ou não temos permissão nem de consultar)
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True     # existe, mas não deu para ler o estado: assume vivo
        return code.value == _STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


class _FILETIME(ctypes.Structure):
    _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]

    def ticks(self) -> int:
        """Os 64 bits em unidades de 100 ns (a granularidade do Win32)."""
        return (self.high << 32) | self.low


# FILETIME conta 100 ns desde 1601-01-01; o epoch Unix fica 11.644.473.600 s depois
_EPOCH_DELTA_TICKS = 116444736000000000
_TICKS_POR_SEGUNDO = 10_000_000


def _process_times(pid: int) -> tuple[float, float] | None:
    """(criação em epoch Unix, CPU acumulada em s) do processo, ou None.

    Uma única chamada a GetProcessTimes serve às duas sondagens - abrir handle é
    o caro aqui. None cobre processo inexistente, sem permissão de consulta ou
    falha da API: quem chama trata "não sei" como ausência de evidência.
    """
    if pid <= 0:
        return None
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        criacao, saida, kernel, usuario = (_FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(handle, ctypes.byref(criacao), ctypes.byref(saida),
                                   ctypes.byref(kernel), ctypes.byref(usuario)):
            return None
        nascido = (criacao.ticks() - _EPOCH_DELTA_TICKS) / _TICKS_POR_SEGUNDO
        cpu = (kernel.ticks() + usuario.ticks()) / _TICKS_POR_SEGUNDO
        return (nascido, cpu)
    finally:
        k32.CloseHandle(handle)


def pid_started_at(pid: int) -> float | None:
    """Instante (epoch Unix) em que o processo NASCEU, ou None se não dá para saber.

    Existe para desmascarar PID RECICLADO: o Windows reaproveita números de PID
    depressa, então "o PID do .lock está vivo" não prova que o dono do lock está
    vivo - pode ser outro processo qualquer que herdou o número. Comparar com o
    carimbo de quando o lock foi criado resolve sem heurística de idade.
    """
    t = _process_times(pid)
    return None if t is None else t[0]


def pid_cpu_seconds(pid: int) -> float | None:
    """CPU acumulada (kernel + usuário, em s) do processo, ou None.

    É o sinal de PROGRESSO do subprocesso de processamento: transcrever queima
    CPU o tempo todo, então CPU parada por minutos a fio significa travado, não
    lento (#176 - o usuário via a reunião "Transcrevendo…" com 0% em tudo).
    """
    t = _process_times(pid)
    return None if t is None else t[1]


def has_nvidia_gpu() -> bool:
    """Driver NVIDIA presente? (nvcuda.dll carrega — sonda histórica do diagnóstico)."""
    import ctypes

    try:
        ctypes.WinDLL("nvcuda.dll")
        return True
    except OSError:
        return False


def ensure_user_path() -> str:
    """No-op: no Windows o PATH do registro chega inteiro no processo de GUI.

    O par POSIX existe porque o launchd entrega um PATH mínimo a app lançado do
    Finder/Dock (o `claude`/`ffmpeg` sumiam do `which`); aqui nada muda — o
    comportamento histórico do Windows fica intocado (#104).
    """
    return os.environ.get("PATH", "")


def open_path(path) -> None:
    """Abre arquivo ou pasta no Explorer (substituto do os.startfile).

    O winrt (toasts) inicializa o COM do processo em MTA, e o ShellExecute do
    os.startfile falha em abrir pastas nesse modo ("o local não está
    disponível"). O explorer.exe em processo próprio não tem esse problema.
    """
    import subprocess

    subprocess.Popen(["explorer.exe", str(path)])


# ------------------------------------------------- instância única (#100) ---
# Código MOVIDO de main.py sem reescrita: a lógica de retry pós-update (#74)
# é delicada — a nova instância espera a antiga liberar o mutex no teardown.

_mutex_handle = None  # mantém o handle vivo durante o processo

_MUTEX_NAME = "Local\\ScribaDevSingleInstance"

# Evento nomeado que a 2ª instância usa para pedir "mostre a janela" à 1ª —
# sem isso, clicar no atalho com o app já na bandeja não fazia nada visível.
_SHOW_EVENT_NAME = "Local\\ScribaDevShowWindow"


def single_instance(retries: int = 0, delay: float = 0.4, name: str = _MUTEX_NAME) -> bool:
    """True se esta é a única instância (adquiriu o mutex nomeado `name`).

    Se o mutex já existe e `retries` > 0, FECHA o handle e tenta de novo após `delay` s
    até `retries` vezes. Isso conserta o reinício pós-update intermitente (#74): o
    relançamento pode chegar enquanto a instância ANTERIOR ainda está no teardown
    (encerra por `os._exit` em até 3 s) segurando o mutex — sem o retry a nova instância
    via ERROR_ALREADY_EXISTS e abortava calada, então o app "não reabria sozinho".

    Importante: quando o mutex já existe, CreateMutexW devolve um handle para o mutex
    EXISTENTE; segurá-lo manteria o objeto vivo e o retry nunca veria a liberação — por
    isso fechamos o handle antes de reesperar."""
    import ctypes

    global _mutex_handle
    k32 = ctypes.windll.kernel32
    for attempt in range(retries + 1):
        _mutex_handle = k32.CreateMutexW(None, False, name)
        if k32.GetLastError() != 183:  # != ERROR_ALREADY_EXISTS → adquirimos
            return True
        if _mutex_handle:
            k32.CloseHandle(_mutex_handle)
            _mutex_handle = None
        if attempt < retries:
            time.sleep(delay)
    return False


def signal_show_window() -> bool:
    """Acorda a instância que já está rodando (True se conseguiu sinalizar)."""
    import ctypes

    EVENT_MODIFY_STATE = 0x0002
    k32 = ctypes.windll.kernel32
    handle = k32.OpenEventW(EVENT_MODIFY_STATE, False, _SHOW_EVENT_NAME)
    if not handle:
        return False
    k32.SetEvent(handle)
    k32.CloseHandle(handle)
    return True


def show_window_listener(stop_event, on_signal) -> None:
    """Loop (para uma daemon-thread): espera o sinal de uma 2ª instância e chama on_signal."""
    import ctypes

    handle = ctypes.windll.kernel32.CreateEventW(None, False, False, _SHOW_EVENT_NAME)
    if not handle:
        log.warning("não consegui criar o evento de ativação")
        return
    while not stop_event.is_set():
        # timeout de 1 s para reavaliar o stop_event; 0 = WAIT_OBJECT_0 (sinalizado)
        if ctypes.windll.kernel32.WaitForSingleObject(handle, 1000) == 0:
            on_signal()


def spawn_relaunch(pid: int, log_path) -> None:
    """Agenda uma nova instância para DEPOIS que `pid` sair (libera o mutex).

    PS que espera este processo sair e sobe a nova instância. Marca
    SCRIBA_RELAUNCHED p/ a nova instância dar retry no single-instance (#74),
    e LOGA cada passo — antes a falha do Start-Process era 100% silenciosa.
    Exceções propagam: o chamador decide como avisar o usuário.
    """
    import subprocess

    pyw = Path(sys.executable)
    if pyw.name.lower() == "python.exe":
        pyw = pyw.with_name("pythonw.exe")  # sem janela de console
    ps = ("$ErrorActionPreference='Continue'; "
          f"Write-Output ('[' + (Get-Date -Format s) + '] relaunch: aguardando pid {pid} sair'); "
          f"Wait-Process -Id {pid} -Timeout 60 -ErrorAction SilentlyContinue; "
          "$env:SCRIBA_RELAUNCHED='1'; "
          "Write-Output 'relaunch: subindo nova instancia'; "
          f"try {{ Start-Process '{pyw}' -ArgumentList '-m','scriba.cli','run' -ErrorAction Stop; "
          "Write-Output 'relaunch: Start-Process OK' } "
          "catch { Write-Output ('relaunch: Start-Process FALHOU - ' + $_.Exception.Message) }")
    logf = open(log_path, "a", encoding="utf-8")
    subprocess.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
        stdout=logf, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0),
    )
