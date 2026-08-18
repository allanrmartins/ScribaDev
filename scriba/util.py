"""Utilidades compartilhadas: pastas do app, CUDA, claude CLI, FILETIME."""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import re
import shutil
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

from . import plat

log = logging.getLogger("scriba.util")

# Diretório de dados por SO via camada de plataforma (#104). No Windows é o
# %LOCALAPPDATA%\ScribaDev de sempre (plat._win); Linux/macOS em plat._posix.
APP_DIR = plat.app_data_dir()
LOGS_DIR = APP_DIR / "logs"
CONFIG_PATH = APP_DIR / "config.toml"
STATE_PATH = APP_DIR / "state.json"
PROMPT_PATH = APP_DIR / "prompt.md"  # instruções (MD) que geram o resumo — editável
CONTEXT_PATH = APP_DIR / "context.md"  # cabeçalho "Contexto para IA" da nota — editável

# Recursos EMPACOTADOS (read-only, vão junto no .exe): ícones e, no futuro, templates.
# Resolvidos de forma compatível com bundle — NUNCA por cwd. Dados do usuário (config,
# notas, gravações) NÃO são recurso: vivem em APP_DIR. Ver Fase 0.2 do empacotamento.
def resource_path(*parts: str) -> Path:
    """Caminho de um recurso empacotado, relativo ao pacote `scriba`.

    Em PyInstaller os dados são extraídos sob `sys._MEIPASS`; no código-fonte (e no
    Nuitka, que mantém `__file__` válido para os dados do pacote) caem ao lado deste
    módulo. ÚNICO ponto a ajustar quando o empacotador for escolhido (Fase 1).
    """
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) / "scriba" if base else Path(__file__).resolve().parent
    return root.joinpath(*parts)


# Ícones (pergaminho escrito por um raio) — empacotados junto do código.
ASSETS_DIR = resource_path("assets")
ICON_ICO = ASSETS_DIR / "scriba.ico"
ICON_PNG = ASSETS_DIR / "scriba.png"
ICON_REC_PNG = ASSETS_DIR / "scriba_rec.png"
ICON_TEMPLATE_PNG = ASSETS_DIR / "scriba_template.png"  # menu bar do macOS (máscara)

def so_nome() -> str:
    """Nome de exibição do SO atual, para textos de UI ("padrão do Windows",
    "segue o macOS"...). Não usar para lógica — para isso é sys.platform."""
    if sys.platform == "win32":
        return "Windows"
    if sys.platform == "darwin":
        return "macOS"
    return "Linux"


# Identidade do app no Windows (taskbar + toasts). Sem ela, a barra de tarefas
# casa a janela (que vive num pythonw.exe filho) com o atalho do IDLE e mostra
# ícone do Python + hint "IDLE (Python 3.x)".
APP_AUMID = "ScribaDev.App"


def set_explicit_app_id(app_id: str = APP_AUMID) -> None:
    """Dá ao processo um AppUserModelID próprio. Chamar ANTES de criar janelas."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass

class Stage(NamedTuple):
    """Um estágio do processamento pós-call (status do meta.json)."""
    label: str          # rótulo p/ a UI (pílula, barra de progresso, faixa da capa)
    fraction: float     # progresso 0..1 (a barra nunca anda p/ trás)
    icon: str           # ícone Fluent na faixa "em andamento" da capa
    in_progress: bool   # ainda não virou nota (aparece como "em andamento")
    is_error: bool = False   # terminal de erro (não pulsa; cor de erro)


# TABELA ÚNICA de estágios: rótulo, progresso, ícone e flags. Adicionar um estágio é
# editar SÓ aqui — as demais estruturas (IN_PROGRESS_STATUSES, ícones, rótulos) derivam
# desta tabela, então nenhum lugar fica pra trás (#94). Ordem = ordem do progresso.
STAGES: dict[str, Stage] = {
    "recording":    Stage("Gravando…",         0.05, "hourglass",  in_progress=True),
    "recorded":     Stage("Na fila…",           0.12, "hourglass",  in_progress=True),
    "transcribing": Stage("Transcrevendo…",     0.40, "edit",       in_progress=True),
    "diarizing":    Stage("Separando vozes…",   0.60, "people",     in_progress=True),
    "transcribed":  Stage("Transcrição pronta", 0.72, "checkmark",  in_progress=True),
    "summarizing":  Stage("Gerando resumo…",    0.88, "sparkle",    in_progress=True),
    "done":         Stage("Pronto",             1.00, "checkmark",  in_progress=False),
    # terminais de erro: in_progress=False (não entram em IN_PROGRESS_STATUSES: nada de
    # loop infinito) mas is_error=True (a capa os mostra em vermelho, sem pulso)
    "failed":       Stage("Falhou",             1.00, "warning",    in_progress=False, is_error=True),
    "no_audio":     Stage("Sem áudio gravado",  1.00, "warning",    in_progress=False, is_error=True),
}
_STAGE_FALLBACK = Stage("Processando…", 0.3, "hourglass", in_progress=False)
# status que ainda não viraram nota (aparecem como "em andamento" na lista)
IN_PROGRESS_STATUSES = tuple(s for s, st in STAGES.items() if st.in_progress)


def stage(status: str | None) -> Stage:
    return STAGES.get(status or "", _STAGE_FALLBACK)


def stage_label(status: str | None) -> str:
    return stage(status).label


def stage_fraction(status: str | None) -> float:
    return stage(status).fraction

_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def ensure_app_dirs() -> None:
    for d in (APP_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def filetime_to_datetime(ft: int) -> datetime:
    """Converte um FILETIME do Windows (100 ns desde 1601) para datetime UTC."""
    return _FILETIME_EPOCH + timedelta(microseconds=ft / 10)


def known_folder(folder_id: str) -> Path | None:
    """Resolve uma pasta conhecida do Windows pelo FOLDERID (respeita OneDrive).

    Sem subprocess/PowerShell de propósito: a saída do PS chega em codepage OEM
    e corrompe caminhos acentuados ("Área de Trabalho").
    """

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    try:
        u = uuid.UUID(folder_id)
        guid = _GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        path_ptr = ctypes.c_wchar_p()
        res = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(path_ptr)
        )
        if res == 0 and path_ptr.value:
            p = Path(path_ptr.value)
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
            return p
    except Exception:
        pass
    return None


def documents_dir() -> Path:
    """Pasta Documentos real do usuário (respeita redirecionamento do OneDrive)."""
    return known_folder("FDD39AD0-238F-46AF-ADB4-6C85480369C7") or Path.home() / "Documents"


_runtime_preloaded = False


def preload_msvc_runtime() -> None:
    """Carrega o runtime C++ do System32 antes de qualquer pacote nativo.

    O pacote winrt (toasts) embute um MSVCP140.dll antigo (14.29); se ele entrar
    no processo primeiro, o ctranslate2 (compilado contra o runtime novo) sofre
    access violation na transcrição. O Windows resolve DLL por nome: quem chega
    primeiro vence — então carregamos a versão nova do sistema antes.
    """
    global _runtime_preloaded
    if _runtime_preloaded:
        return
    _runtime_preloaded = True
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    for dll in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        p = system32 / dll
        if p.exists():
            try:
                ctypes.WinDLL(str(p))
            except OSError:
                pass


_dll_dirs_added = False


def bootstrap_cuda_dlls() -> None:
    """Disponibiliza as DLLs CUDA instaladas via pip (cublas/cudnn/nvrtc).

    O ctranslate2 carrega cublas64_12.dll com LoadLibrary simples, que só
    consulta o PATH do processo — os.add_dll_directory sozinho não basta.
    """
    if sys.platform != "win32":
        # layout Lib/site-packages/nvidia/*/bin e os.add_dll_directory são do
        # Windows; no Linux o CUDA chega pelos wheels (RPATH), no macOS não existe (#98)
        return
    global _dll_dirs_added
    if _dll_dirs_added:
        return
    _dll_dirs_added = True
    nv = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    bins = [str(b) for b in nv.glob("*/bin") if b.is_dir()]
    if not bins:
        return
    os.environ["PATH"] = os.pathsep.join(bins) + os.pathsep + os.environ.get("PATH", "")
    for b in bins:
        os.add_dll_directory(b)


def open_path(path) -> None:
    """Abre arquivo ou pasta no gerenciador do SO (Explorer / xdg-open / open).

    O porquê do explorer.exe em processo próprio no Windows (COM em MTA do
    winrt) vive em plat._win.open_path (#104).
    """
    plat.open_path(path)


def reveal_path(path) -> None:
    """Abre o gerenciador de arquivos COM o arquivo já SELECIONADO (win/mac) — o
    'Reportar erro' deixa o zip pronto p/ arrastar na issue. Fallback: abre a pasta."""
    import subprocess

    p = Path(path)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{p}"])
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
            return
    except Exception:
        log.debug("reveal_path falhou — abrindo a pasta", exc_info=True)
    open_path(p.parent)


def console_python() -> Path:
    """python de CONSOLE do venv para subprocessos (não o pythonw da GUI).

    No Windows a GUI roda sob pythonw.exe e sys.executable herdaria a falta de
    console (pipes/encoding quebram) — por isso o python.exe irmão em Scripts/.
    No POSIX não existe essa distinção (nem Scripts/): sys.executable resolve (#96).
    """
    if sys.platform == "win32":
        return Path(sys.prefix) / "Scripts" / "python.exe"
    return Path(sys.executable)


def frozen_console_exe() -> Path | None:
    """Na instalação CONGELADA, o executável de CONSOLE irmão no bundle
    (`scribadev`/`scribadev.exe`); None fora do bundle (ou se ele faltar).

    É ele — não o `sys.executable`, que na GUI é o windowed sem stdout — quem roda
    os subprocessos do app. Os dois exes saem do mesmo COLLECT e moram na mesma
    pasta (ver installer/*/…spec).
    """
    if not getattr(sys, "frozen", False):
        return None
    nome = "scribadev.exe" if sys.platform == "win32" else "scribadev"
    exe = Path(sys.executable).resolve().parent / nome
    return exe if exe.exists() else None


def app_command(*args: str) -> list[str]:
    """argv para rodar um subcomando do ScribaDev num processo NOVO.

    Congelado: `[<bundle>/scribadev, *args]`. Um exe congelado NÃO entende
    `-m módulo` — os argumentos vão inteiros para o argparse do app. Era esse o bug
    da instalação por instalador/DMG: `[sys.executable, "-X", "utf8", "-m",
    "scriba.cli", "process", pasta]` virava "unrecognized arguments" e NENHUMA
    gravação era transcrita (o meta ia para "failed" sem explicação).

    Fonte/venv: `[<python de console>, -X utf8, -m scriba.cli, *args]` — o de sempre.
    """
    exe = frozen_console_exe()
    if exe is not None:
        return [str(exe), *args]
    return [str(console_python()), "-X", "utf8", "-m", "scriba.cli", *args]


def _audio_probe(extra_args: list[str], timeout: float) -> dict | None:
    """Roda a sonda de áudio (scriba.audioprobe) num subprocesso descartável — o
    PortAudio pode abortar o processo com assert de CRT ao enumerar. None se falhar."""
    import json
    import subprocess

    try:
        proc = subprocess.run(
            app_command("audioprobe", *extra_args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        pass
    return None


def run_audio_probe(timeout: float = 12.0) -> dict | None:
    """Nomes do mic/loopback padrão via subprocesso (ver audioprobe.py). None se indisponível."""
    return _audio_probe([], timeout)


def list_audio_devices(timeout: float = 15.0) -> dict | None:
    """Listas de mics e loopbacks (nomes) via subprocesso, para os seletores da UI:
    {'mics':[...], 'loopbacks':[...], 'default_mic':str, 'default_loopback':str} ou None."""
    return _audio_probe(["list"], timeout)


def quiet_crt_asserts() -> None:
    """Suprime os diálogos modais de erro/assert do CRT (ex.: PortAudio) no processo."""
    try:
        # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    except Exception:
        pass
    for crt in ("ucrtbase", "msvcrt"):
        try:
            lib = ctypes.WinDLL(crt)
            lib._set_abort_behavior(0, 3)  # _WRITE_ABORT_MSG | _CALL_REPORTFAULT
        except Exception:
            pass


_FS_INVALID = set('<>:"/\\|?*')
# prefixo de data/hora dos nomes de pasta de gravação: "16-34", "16-34_2" (mesmo
# minuto) e o legado "2026-06-12_16-34[_2]" — o que vem depois é título anexado
_FOLDER_TIME_PREFIX = re.compile(r"^(?:\d{4}-\d{2}-\d{2}_)?\d{2}-\d{2}(?:_\d+)?")


def safe_title_for_fs(title: str, max_len: int = 60) -> str:
    """Título seguro para nome de pasta no Windows (sem <>:\"/\\|?*, sem fim em '. ')."""
    cleaned = "".join(c for c in (title or "") if c not in _FS_INVALID and ord(c) >= 32)
    cleaned = " ".join(cleaned.split())
    return cleaned[:max_len].rstrip(" .")


def rename_recording_folder(folder: Path, title: str) -> Path:
    """Anexa o título da nota ao nome da pasta da gravação: HH-MM → HH-MM_Título.

    Idempotente: refeito com outro título, troca o anexo em vez de empilhar.
    Devolve o novo Path (ou o original se não há título/prefixo ou o rename falhar
    — ex.: arquivo aberto; nada no fluxo depende do nome novo).
    """
    folder = Path(folder)
    safe = safe_title_for_fs(title)
    m = _FOLDER_TIME_PREFIX.match(folder.name)
    if not safe or m is None or not folder.is_dir():
        return folder
    target = folder.with_name(f"{m.group(0)}_{safe}")
    if target == folder:
        return folder
    try:
        folder.rename(target)
        return target
    except OSError:
        return folder


def ffmpeg_command() -> list[str] | None:
    """Prefixo de comando para invocar o ffmpeg, ou None se não estiver no PATH.

    Usado para compactar o áudio guardado (WAV cru → opus/flac 16 kHz mono) e,
    numa re-transcrição, decodificar esse áudio comprimido para a diarização.
    """
    exe = shutil.which("ffmpeg")
    return [exe] if exe else None


def ffmpeg_status(keep_audio: bool, archive_format: str) -> str:
    """Nível de saúde do ffmpeg p/ a compressão do áudio guardado — regra ÚNICA usada
    pelo `scriba doctor` e pela aba Sobre:
      'ok'   — ffmpeg no PATH;
      'err'  — ausente E o usuário quer guardar áudio comprimido (keep + opus/flac):
               é aí que os .wav ficam gigantes (~1,3 GB/h em vez de ~20 MB/h);
      'warn' — ausente, mas sem impacto imediato (não guarda áudio, ou formato wav).
    """
    if shutil.which("ffmpeg"):
        return "ok"
    fmt = (archive_format or "wav").strip().lower()
    return "err" if (keep_audio and fmt in ("opus", "flac")) else "warn"


def claude_command() -> list[str] | None:
    """Prefixo de comando para invocar o claude CLI, ou None se não instalado.

    Instalações via npm expõem claude.cmd, que o CreateProcess não executa
    diretamente — nesse caso roteia por cmd /c.
    """
    exe = shutil.which("claude")
    if not exe:
        return None
    if exe.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", exe]
    return [exe]


# -- datas no formato BR (máscara/validação dos filtros de busca) ------------

def format_date_br(raw: str) -> str:
    """Máscara DD/MM/AAAA: mantém só os dígitos (até 8) e insere as barras. Funciona
    em qualquer ponto da digitação. '19021988' e '19/02/1988' → '19/02/1988'; texto
    não-numérico ('aqsas') é descartado."""
    digits = re.sub(r"\D", "", raw or "")[:8]
    out = digits[:2]
    if len(digits) > 2:
        out += "/" + digits[2:4]
    if len(digits) > 4:
        out += "/" + digits[4:8]
    return out


def date_br_to_iso(s: str) -> str:
    """'DD/MM/AAAA' VÁLIDA → 'AAAA-MM-DD'. Incompleta ou inválida (31/02/2020,
    99/99/9999, 19/21/8890…) → "" — o chamador trata como 'sem filtro'."""
    try:
        return datetime.strptime((s or "").strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def format_time_hhmm(raw: str) -> str:
    """Máscara HH:MM: mantém só os dígitos (até 4) e insere ':' após 2. '0930' e
    '09:30' → '09:30'; texto não-numérico é descartado."""
    digits = re.sub(r"\D", "", raw or "")[:4]
    return digits if len(digits) <= 2 else digits[:2] + ":" + digits[2:]


def time_hhmm_ok(s: str) -> bool:
    """True se `s` for uma hora 'HH:MM' válida (00:00–23:59)."""
    try:
        datetime.strptime((s or "").strip(), "%H:%M")
        return True
    except ValueError:
        return False


def date_range_filter(since_br: str, until_br: str) -> tuple[str | None, str | None]:
    """Semântica do filtro de período da busca (datas em DD/MM/AAAA):

    - só DE válido  → aquele DIA exato (since == until == DE);
    - só ATÉ válido → aquele DIA exato;
    - DE e ATÉ      → INTERVALO; a ordem não importa (ajusta para min..max).

    Datas inválidas/parciais são ignoradas. Devolve (since_iso, until_iso) em ISO
    ('AAAA-MM-DD'), cada um None quando não há filtro aplicável."""
    lo = date_br_to_iso(since_br)
    hi = date_br_to_iso(until_br)
    if lo and hi:
        return tuple(sorted((lo, hi)))  # intervalo, ordem indiferente
    if lo:
        return lo, lo                   # só DE → aquele dia
    if hi:
        return hi, hi                   # só ATÉ → aquele dia
    return None, None


def atomic_write_text(path: Path, data: str, encoding: str = "utf-8") -> None:
    """Grava `data` em `path` de forma atômica: escreve num .tmp na mesma pasta
    e faz os.replace() — atômico no Windows contra kill de processo e antivírus.

    O temporário usa `path.name + '.tmp'` (e não with_suffix) para evitar
    colisão/extensão errada: 'transcript.json.tmp', não 'transcript.tmp'.
    Em erro na gravação do .tmp, remove o parcial e não toca o destino.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(data, encoding=encoding)
        # flush + fsync antes do replace: endurece contra corte de energia
        # (o ganho principal já vem do replace atômico; o fsync é extra defensivo)
        with tmp.open("r+b") as f:
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # remove o .tmp parcial para não deixar lixo — o destino fica intacto
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# -- estado leve do app (state.json): preferências de conveniência, não config --
# Ex.: posição da pílula (overlay.py) e o último nº de participantes informado.
# Perdê-lo nunca quebra nada — por isso tudo aqui é silencioso em erro.

def read_state() -> dict:
    """Lê o state.json. {} se ausente/ilegível."""
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def update_state(**values) -> None:
    """Mescla `values` no state.json (escrita atômica, preserva as outras chaves)."""
    try:
        data = read_state()
        data.update(values)
        atomic_write_text(STATE_PATH, json.dumps(data))
    except Exception:
        pass


# lock criado há mais de 2 h sem PID vivo é considerado órfão de crash
LOCK_MAX_AGE_SECONDS = 7200


def is_locked(folder: Path) -> bool:
    """True se a pasta tem um `.lock` ATIVO (worker em andamento).

    Ativo = o arquivo `.lock` existe E (o PID gravado ainda está vivo no sistema
    OU o lock tem menos de LOCK_MAX_AGE_SECONDS). Lock sem PID vivo E mais antigo
    que o limite é tratado como órfão de crash (ignorado). Lock ilegível: assume
    ativo por precaução (melhor pular do que reprocessar/apagar em cima).

    Fonte única usada pela retenção (não apagar pasta em uso) e pelo pipeline
    (não reprocessar pasta já em andamento por outro processo — ex.: worker da GUI
    + `scriba process` manual ao mesmo tempo).
    """
    lock_path = folder / ".lock"
    if not lock_path.exists():
        return False
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(data.get("pid", 0))
        started = float(data.get("started", 0))
    except Exception:
        return True

    age = time.time() - started
    if age > LOCK_MAX_AGE_SECONDS:
        # lock muito antigo: só é genuíno se o PID ainda existir
        if pid:
            try:
                os.kill(pid, 0)  # sinal 0: só verifica existência do processo
                return True
            except (OSError, PermissionError):
                pass  # processo morreu: lock órfão
        log.debug("ignorando lock órfão em %s (pid=%s, %.0f min)", folder.name, pid, age / 60)
        return False
    return True  # lock recente — respeitar mesmo sem verificar o PID


def virtual_screen_rect() -> tuple[int, int, int, int] | None:
    """Retângulo que engloba TODOS os monitores: (x, y, largura, altura) em
    coordenadas virtuais do Windows — inclui telas à esquerda/acima do primário
    (coordenadas negativas). None se indisponível (não-Windows ou falha de ctypes).

    Via GetSystemMetrics (SM_*VIRTUALSCREEN): uma chamada barata, NÃO a enumeração
    completa de monitores. Suficiente para detectar uma posição salva que caiu
    fora de qualquer tela (monitor desconectado).
    """
    try:
        gsm = ctypes.windll.user32.GetSystemMetrics
        # SM_XVIRTUALSCREEN=76 · SM_YVIRTUALSCREEN=77 · SM_CXVIRTUALSCREEN=78 · SM_CYVIRTUALSCREEN=79
        x, y, w, h = gsm(76), gsm(77), gsm(78), gsm(79)
        if w > 0 and h > 0:
            return (x, y, w, h)
    except Exception:
        pass
    return None


# Segredos (chaves de API) cifrados com a DPAPI do Windows — escopo: o usuário atual
# desta máquina. O valor gravado é "dpapi:<base64>"; só este usuário/máquina decifra.
DPAPI_PREFIX = "dpapi:"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_encrypt(plaintext: str) -> str | None:
    """Cifra `plaintext` com a DPAPI (CryptProtectData). Devolve 'dpapi:<base64>' ou
    None se indisponível (não-Windows / falha) — o chamador grava plaintext nesse caso."""
    if not plaintext:
        return None
    try:
        data = plaintext.encode("utf-8")
        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return None
        try:
            enc = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return DPAPI_PREFIX + base64.b64encode(enc).decode("ascii")
    except Exception:
        return None


def dpapi_decrypt(token: str) -> str | None:
    """Decifra um 'dpapi:<base64>' (CryptUnprotectData). None se falhar — token de
    outro usuário/máquina, corrompido, ou DPAPI indisponível."""
    if not token or not token.startswith(DPAPI_PREFIX):
        return None
    try:
        enc = base64.b64decode(token[len(DPAPI_PREFIX):])
        buf = ctypes.create_string_buffer(enc, len(enc))
        blob_in = _DataBlob(len(enc), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return None
        try:
            dec = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return dec.decode("utf-8")
    except Exception:
        return None


# Segredos no macOS: Keychain via /usr/bin/security (#104). O TOML guarda só a
# REFERÊNCIA "keychain:<conta>"; o segredo vive no chaveiro de login, service
# "ScribaDev". O binário security entra na ACL do item ao criá-lo, então as
# leituras seguintes não abrem prompt. Mesmo contrato de degradação da DPAPI:
# qualquer falha devolve None e o chamador grava/lê plaintext.
KEYCHAIN_PREFIX = "keychain:"
_KEYCHAIN_SERVICE = "ScribaDev"


def _keychain_texto_seguro(s: str) -> bool:
    """O tokenizer do `security -i` entende aspas duplas com escapes, mas segredo
    é coisa séria demais para apostar em escaping: só aceitamos conta/segredo sem
    aspas, barras ou controle (chaves de API reais são ASCII simples). Fora disso,
    degrada para plaintext em vez de gravar um valor possivelmente truncado."""
    return bool(s) and not any(c in s for c in '"\\\n\r\t') and s.isprintable()


def keychain_store(account: str, secret: str) -> str | None:
    """Guarda `secret` no Keychain (service ScribaDev, conta `account`) e devolve
    o token 'keychain:<conta>' para gravar no TOML. None se indisponível (não-macOS,
    chaveiro trancado, texto arriscado) — o chamador grava plaintext nesse caso.

    O comando vai por STDIN (`security -i`), nunca por argv: argumentos de
    processo são visíveis no `ps` de qualquer usuário."""
    if sys.platform != "darwin" or not _keychain_texto_seguro(account) or not _keychain_texto_seguro(secret):
        return None
    import subprocess

    try:
        cmd = f'add-generic-password -U -s "{_KEYCHAIN_SERVICE}" -a "{account}" -w "{secret}"\n'
        out = subprocess.run(["/usr/bin/security", "-i"], input=cmd.encode("utf-8"),
                             capture_output=True, timeout=15)
        if out.returncode != 0:
            log.warning("keychain_store falhou p/ %s (rc=%s) — gravando plaintext", account, out.returncode)
            return None
        return KEYCHAIN_PREFIX + account
    except Exception as e:
        log.warning("keychain_store indisponível (%s) — gravando plaintext", e)
        return None


def keychain_lookup(token: str) -> str | None:
    """Resolve um 'keychain:<conta>' lendo o Keychain. None se falhar (item
    removido, chaveiro trancado, não-macOS) — chave inutilizável nesta máquina."""
    if sys.platform != "darwin" or not token or not token.startswith(KEYCHAIN_PREFIX):
        return None
    account = token[len(KEYCHAIN_PREFIX):]
    if not _keychain_texto_seguro(account):
        return None
    import subprocess

    try:
        out = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-a", account, "-w"],
            capture_output=True, timeout=15,
        )
        if out.returncode != 0:
            return None
        return out.stdout.decode("utf-8").rstrip("\n") or None
    except Exception:
        return None


def keychain_ok() -> bool:
    """O Keychain responde? (sonda do doctor). `find-generic-password` do service
    ScribaDev: rc 0 (já há item) e rc 44 (nada gravado ainda) contam como OK;
    qualquer outro rc/erro = chaveiro inacessível."""
    if sys.platform != "darwin":
        return False
    import subprocess

    try:
        out = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", _KEYCHAIN_SERVICE],
                             capture_output=True, timeout=15)
        return out.returncode in (0, 44)
    except Exception:
        return False
