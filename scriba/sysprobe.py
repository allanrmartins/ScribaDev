"""Sonda de hardware + recomendação de configuração (#147, wizard de 1º uso).

Duas metades bem separadas:

- `probe()` — MEDE a máquina (GPU NVIDIA/VRAM via nvidia-smi best-effort, Apple
  Silicon, RAM, núcleos, disco livre). Nunca levanta: campo indisponível vira
  None/False e a recomendação degrada com segurança.
- `recommend(p)` — PURA e testável: dado um retrato da máquina, devolve o que o
  wizard deve PRÉ-SELECIONAR (modelo do Whisper, dispositivo, diarização) com as
  razões em PT-BR — o wizard mostra as razões, e o usuário pode trocar tudo na
  instalação Avançada.

Heurísticas de docs/modos-ia-requisitos-hardware.md: o large-v3-turbo int8 pede
~3 GB de VRAM (GPU) e é 10-20x mais lento em CPU (onde small/medium rendem mais);
a diarização (torch+pyannote) só é confortável com GPU.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from . import util

log = logging.getLogger("scriba.sysprobe")

# tamanhos aproximados de download por modelo (int8, cache HF) — exibidos no wizard
MODEL_DOWNLOAD_MB = {
    "tiny": 75, "base": 140, "small": 460, "medium": 1500, "large-v3-turbo": 1500,
}


@dataclass
class Probe:
    os_name: str = sys.platform
    cpu_cores: int | None = None
    ram_gb: float | None = None
    disk_free_gb: float | None = None
    gpu_nvidia: bool = False
    vram_mb: int | None = None
    apple_silicon: bool = False


@dataclass
class Recommendation:
    whisper_model: str = "small"
    device: str = "cpu"              # cuda | mlx | cpu
    diarization: str = "opcional"    # recomendada | opcional | desaconselhada
    needs_cuda_libs: bool = False    # download das libs NVIDIA (só instalação congelada)
    reasons: list[str] = field(default_factory=list)


def _nvidia_vram_mb() -> int | None:
    """VRAM total (MB) da 1ª GPU via nvidia-smi; None sem GPU/driver. Mesmo padrão
    best-effort do respiro térmico da diarização (diarize._gpu_temp)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode != 0:
            return None
        return int(float(out.stdout.strip().splitlines()[0]))
    except Exception:
        return None


def _ram_gb() -> float | None:
    """RAM física total em GB, sem dependência nova (ctypes no Windows, sysctl no
    mac, /proc/meminfo no Linux). None se não der."""
    try:
        if sys.platform == "win32":
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = _MemStatus()
            st.dwLength = ctypes.sizeof(st)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return round(st.ullTotalPhys / 1024**3, 1)
            return None
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                 text=True, timeout=5)
            return round(int(out.stdout.strip()) / 1024**3, 1)
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024**2, 1)
    except Exception:
        pass
    return None


def _nvidia_smi(args: list[str]) -> list[list[str]] | None:
    """Linhas CSV de uma consulta ao nvidia-smi; None sem GPU/driver. Best-effort
    com timeout curto: sonda de diagnóstico nunca pode segurar o app."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, *args, "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode != 0:
            return None
        return [[c.strip() for c in ln.split(",")]
                for ln in out.stdout.strip().splitlines() if ln.strip()]
    except Exception:
        return None


def gpu_snapshot() -> dict | None:
    """Foto da GPU AGORA: identidade (nome/driver), memória, carga e quem a usa.

    Existe por causa dos #182/#183: dois usuários travando a transcrição CUDA e o
    diagnóstico dizendo só "GPU NVIDIA: sim" - sem modelo, driver, nem se havia
    carga ou um processo órfão segurando a VRAM no momento. None sem GPU/driver.
    """
    linhas = _nvidia_smi(["--query-gpu=name,driver_version,memory.total,memory.used,"
                          "memory.free,utilization.gpu,temperature.gpu"])
    if not linhas or len(linhas[0]) < 7:
        return None
    nome, driver, total, usada, livre, uso, temp = linhas[0][:7]
    snap = {"name": nome, "driver": driver, "vram_total_mb": _int(total),
            "vram_used_mb": _int(usada), "vram_free_mb": _int(livre),
            "util_pct": _int(uso), "temp_c": _int(temp), "procs": []}
    # quem está com contexto CUDA aberto: é aqui que um órfão de transcrição
    # segurando VRAM aparece pelo nome e PID. Só entra quem tem used_memory
    # NUMÉRICO: no Windows (WDDM) a consulta devolve também todo processo com
    # contexto meramente gráfico (navegador, Teams, explorer), todos com N/A -
    # dezenas de linhas que são ruído e expõem os apps do usuário no zip. E só
    # o basename do exe: o caminho completo não acrescenta nada à análise.
    for p in _nvidia_smi(["--query-compute-apps=pid,process_name,used_memory"]) or []:
        if len(p) >= 3 and _int(p[2]) is not None:
            snap["procs"].append({"pid": _int(p[0]),
                                  "name": p[1].replace("\\", "/").rsplit("/", 1)[-1],
                                  "mem_mb": _int(p[2])})
    return snap


def _int(s) -> int | None:
    try:
        return int(float(str(s).strip()))
    except (ValueError, TypeError):
        return None


def ram_snapshot() -> dict | None:
    """RAM e paginação AGORA (Windows): total/disponível físico e pagefile.

    O pagefile é o que denuncia pressão de memória - um modelo grande demais
    "transbordando" para a RAM/disco aparece aqui antes de aparecer em qualquer
    log. Nos outros SOs devolve só o total (o caso de suporte é o Windows)."""
    if sys.platform != "win32":
        total = _ram_gb()
        return {"total_gb": total} if total else None
    try:
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = _MemStatus()
        st.dwLength = ctypes.sizeof(st)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        gb = 1024 ** 3
        return {"total_gb": round(st.ullTotalPhys / gb, 1),
                "avail_gb": round(st.ullAvailPhys / gb, 1),
                "load_pct": int(st.dwMemoryLoad),
                "pagefile_total_gb": round(st.ullTotalPageFile / gb, 1),
                "pagefile_avail_gb": round(st.ullAvailPageFile / gb, 1)}
    except Exception:
        return None


def snapshot_line() -> str:
    """GPU + RAM em UMA linha de log, para o instante de um travamento.

    O watchdog do processamento a anexa ao declarar um subprocesso travado
    (#182/#183): é a única foto do momento real do problema - o Diagnóstico só
    roda depois, quando a carga já mudou. Nunca levanta; sonda indisponível vira
    texto dizendo isso.
    """
    partes = []
    try:
        gs = gpu_snapshot()
    except Exception:
        gs = None
    if gs:
        procs = ", ".join(f"pid {p['pid']} {p['name']} {p['mem_mb']}MB" for p in gs["procs"])
        partes.append(f"GPU {gs['name']} driver {gs['driver']}: uso {gs['util_pct']}%, "
                      f"VRAM {gs['vram_used_mb']}/{gs['vram_total_mb']} MB, {gs['temp_c']}C, "
                      f"procs CUDA: [{procs or 'nenhum'}]")
    else:
        partes.append("GPU: nvidia-smi indisponivel")
    try:
        ram = ram_snapshot()
    except Exception:
        ram = None
    if ram and "avail_gb" in ram:
        partes.append(f"RAM {ram['load_pct']}% em uso ({ram['avail_gb']}/{ram['total_gb']} GB "
                      f"livres), pagefile {ram['pagefile_avail_gb']}/{ram['pagefile_total_gb']} GB livres")
    return " · ".join(partes)


def probe() -> Probe:
    """Retrato da máquina. Nunca levanta — campos indisponíveis ficam None/False."""
    p = Probe()
    p.cpu_cores = os.cpu_count()
    p.ram_gb = _ram_gb()
    try:
        p.disk_free_gb = round(shutil.disk_usage(str(util.APP_DIR.anchor or "/")).free / 1024**3, 1)
    except Exception:
        p.disk_free_gb = None
    if sys.platform == "darwin":
        import platform

        p.apple_silicon = platform.machine() == "arm64"
    else:
        p.vram_mb = _nvidia_vram_mb()
        p.gpu_nvidia = p.vram_mb is not None
    return p


def recommend(p: Probe) -> Recommendation:
    """(pura) Pré-seleção do wizard a partir do retrato da máquina, com as razões."""
    r = Recommendation()
    ram = p.ram_gb or 0
    cores = p.cpu_cores or 0

    # dispositivo + modelo de transcrição
    if p.gpu_nvidia and (p.vram_mb or 0) >= 3000:
        r.device, r.whisper_model = "cuda", "large-v3-turbo"
        r.needs_cuda_libs = True
        r.reasons.append(
            f"GPU NVIDIA com {round((p.vram_mb or 0) / 1024, 1)} GB de VRAM: dá para o melhor "
            "modelo (large-v3-turbo) quase em tempo real.")
    elif p.apple_silicon:
        r.device, r.whisper_model = "mlx", "large-v3-turbo"
        r.reasons.append("Apple Silicon: transcrição acelerada por Metal (MLX) com o melhor modelo.")
    else:
        r.device = "cpu"
        if p.gpu_nvidia:
            r.reasons.append("GPU NVIDIA com pouca VRAM (<3 GB): transcrição fica na CPU.")
        if ram >= 16 and cores >= 8:
            r.whisper_model = "medium"
            r.reasons.append("Sem GPU dedicada: o modelo medium é o melhor equilíbrio "
                             "qualidade/velocidade nesta CPU.")
        elif ram >= 8:
            r.whisper_model = "small"
            r.reasons.append("Sem GPU dedicada: o modelo small mantém a transcrição rápida na CPU.")
        else:
            r.whisper_model = "tiny"
            r.reasons.append(f"Memória limitada ({ram:.0f} GB): o modelo tiny garante fluidez; "
                             "dá para trocar depois nas Configurações.")

    # diarização (separação de vozes): torque de GPU faz diferença
    if p.gpu_nvidia and (p.vram_mb or 0) >= 4000:
        r.diarization = "recomendada"
        r.reasons.append("Separação de vozes recomendada: sua GPU dá conta sem esforço.")
    elif p.apple_silicon or ram >= 16:
        r.diarization = "opcional"
        r.reasons.append("Separação de vozes disponível, mas mais lenta nesta máquina — opcional.")
    else:
        r.diarization = "desaconselhada"
        r.reasons.append("Separação de vozes desaconselhada aqui (sem GPU e pouca memória) — "
                         "dá para ativar depois nas Configurações.")

    # disco: aviso quando o total recomendado não cabe com folga
    need_gb = (MODEL_DOWNLOAD_MB.get(r.whisper_model, 1500) / 1024
               + (3 if r.needs_cuda_libs else 0) + (3 if r.diarization == "recomendada" else 0))
    if p.disk_free_gb is not None and p.disk_free_gb < need_gb + 2:
        r.reasons.append(f"Atenção: só {p.disk_free_gb:.0f} GB livres em disco — os downloads "
                         f"recomendados somam ~{need_gb:.0f} GB.")
    return r
