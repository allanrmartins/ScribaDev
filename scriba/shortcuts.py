"""Atalhos do ScribaDev (.lnk) na Área de Trabalho e no menu Iniciar.

Apontam para o scribadev-tray.exe (sem console) e usam o ícone do app. Sem admin:
tudo no perfil do usuário, criado via WScript.Shell (mesma abordagem do autostart).
Cada .lnk também recebe a propriedade System.AppUserModel.ID = ScribaDev.App: é ela
que faz "Fixar na barra de tarefas" agrupar com a janela do app (que declara o
mesmo AUMID) em vez de cair no ícone/hint do Python.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import POINTER, byref, c_int, c_uint, c_ulong, c_void_p, c_wchar_p
from pathlib import Path

from . import util

# ---- COM mínimo para escrever System.AppUserModel.ID num .lnk ----------------


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, s: str):
        super().__init__()
        ctypes.oledll.ole32.CLSIDFromString(s, byref(self))


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", ctypes.c_uint32)]


class _PROPVARIANT(ctypes.Structure):
    # vt + 3 reservados + união (só usamos pwszVal); _pad completa os 24 bytes x64
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("r1", ctypes.c_ushort),
        ("r2", ctypes.c_ushort),
        ("r3", ctypes.c_ushort),
        ("pwszVal", c_wchar_p),
        ("_pad", ctypes.c_size_t),
    ]


_VT_LPWSTR = 31
_CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"
_IID_IPersistFile = "{0000010B-0000-0000-C000-000000000046}"
_IID_IPropertyStore = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"
_PKEY_AppUserModel_ID = ("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}", 5)


def _com_method(obj: c_void_p, index: int, *argtypes, restype=None):
    """Resolve o método `index` da vtable de `obj` (COM cru, sem comtypes).

    restype default = ctypes.HRESULT, resolvido em TEMPO DE CHAMADA: como default
    de parâmetro (import-time) quebrava o import do módulo no POSIX, onde
    ctypes.HRESULT não existe (#102)."""
    if restype is None:
        restype = ctypes.HRESULT
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(vtbl[index])


def set_shortcut_app_id(lnk: Path, app_id: str = util.APP_AUMID) -> bool:
    """Grava (e confere) System.AppUserModel.ID no .lnk. True se ficou correto."""
    ole32 = ctypes.oledll.ole32
    ole32.CoInitialize(None)
    try:
        pkey = _PROPERTYKEY(_GUID(_PKEY_AppUserModel_ID[0]), _PKEY_AppUserModel_ID[1])
        pf = c_void_p()
        ole32.CoCreateInstance(
            byref(_GUID(_CLSID_ShellLink)), None, 1,  # CLSCTX_INPROC_SERVER
            byref(_GUID(_IID_IPersistFile)), byref(pf),
        )
        try:
            _com_method(pf, 5, c_wchar_p, c_uint)(pf, str(lnk), 2)  # Load, STGM_READWRITE
            ps = c_void_p()
            _com_method(pf, 0, POINTER(_GUID), POINTER(c_void_p))(
                pf, byref(_GUID(_IID_IPropertyStore)), byref(ps)
            )
            try:
                pv = _PROPVARIANT()
                pv.vt = _VT_LPWSTR
                pv.pwszVal = app_id
                _com_method(ps, 6, POINTER(_PROPERTYKEY), POINTER(_PROPVARIANT))(ps, byref(pkey), byref(pv))
                _com_method(ps, 7)(ps)  # Commit
                # confere lendo de volta
                out = _PROPVARIANT()
                _com_method(ps, 5, POINTER(_PROPERTYKEY), POINTER(_PROPVARIANT))(ps, byref(pkey), byref(out))
                ok = out.vt == _VT_LPWSTR and out.pwszVal == app_id
                ole32.PropVariantClear(byref(out))
            finally:
                _com_method(ps, 2, restype=c_ulong)(ps)  # Release
            _com_method(pf, 6, c_wchar_p, c_int)(pf, None, 1)  # Save no próprio arquivo
        finally:
            _com_method(pf, 2, restype=c_ulong)(pf)  # Release
        return ok
    except OSError as e:
        print(f"aviso: não consegui gravar o AppUserModelID em {lnk.name} ({e})")
        return False
    finally:
        ole32.CoUninitialize()


def tray_exe() -> Path:
    return Path(sys.prefix) / "Scripts" / "scribadev-tray.exe"


def _create(lnk: Path, target: Path, icon: Path) -> bool:
    """Cria/atualiza um .lnk via WScript.Shell. True se OK."""
    lnk.parent.mkdir(parents=True, exist_ok=True)
    icon_line = f"$s.IconLocation = '{icon}'; " if icon.exists() else ""
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{lnk}'); "
        f"$s.TargetPath = '{target}'; "
        f"$s.WorkingDirectory = '{target.parent}'; "
        f"{icon_line}"
        "$s.Description = 'ScribaDev - gravacao automatica de calls do Teams'; "
        "$s.Save()"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True,
        text=True,
        errors="replace",  # stderr do PS vem em codepage OEM; não pode derrubar o comando
    )
    if proc.returncode != 0:
        print(f"falha ao criar {lnk.name}: {proc.stderr.strip()}")
        return False
    return True


# FOLDERIDs (SHGetKnownFolderPath) — Desktop e Iniciar>Programas do usuário
_FOLDERID_DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"
_FOLDERID_PROGRAMS = "A77F5D77-2E2B-44C3-A6A2-ABA601054A51"


def create_shortcuts(desktop: bool = True, start_menu: bool = True) -> int:
    """Cria os atalhos pedidos. Retorna 0 se ao menos um foi criado."""
    if sys.platform != "win32":
        # .lnk/COM/AUMID são do Windows; .desktop/aliases vêm em marcos futuros (#104)
        print("atalhos não suportados neste SO ainda (Windows-only por ora)")
        return 1
    target = tray_exe()
    if not target.exists():
        print(f"não encontrei {target} — rode o setup.ps1 de novo")
        return 1

    made = 0
    if desktop:
        d = util.known_folder(_FOLDERID_DESKTOP)
        if d and _create(d / "ScribaDev.lnk", target, util.ICON_ICO):
            aumid = set_shortcut_app_id(d / "ScribaDev.lnk")
            print(f"atalho na Área de Trabalho: {d / 'ScribaDev.lnk'}" + ("" if aumid else " (sem AUMID)"))
            made += 1
    if start_menu:
        p = util.known_folder(_FOLDERID_PROGRAMS)
        if p and _create(p / "ScribaDev.lnk", target, util.ICON_ICO):
            aumid = set_shortcut_app_id(p / "ScribaDev.lnk")
            print(f"atalho no menu Iniciar: {p / 'ScribaDev.lnk'}" + ("" if aumid else " (sem AUMID)"))
            made += 1
    if not made:
        print("nenhum atalho criado")
        return 1
    return 0


# ---- reparo de ícone quebrado (#192) -----------------------------------------
# O IconLocation dos .lnk é o caminho ABSOLUTO de scriba/assets/scriba.ico dentro
# do repositório (instalação por código-fonte). Repo movido = arquivo sumiu = a
# janela aparece na barra de tarefas com o ícone genérico de documento, porque o
# atalho fixado tem o mesmo AUMID da janela e o shell usa o ícone DELE. O app
# conserta no boot: só os atalhos cujo alvo é o tray DESTA instalação.


def _pinned_taskbar_dir() -> Path:
    """Pasta dos atalhos fixados na barra de tarefas (não tem FOLDERID próprio)."""
    return Path(os.environ.get("APPDATA", "")) / r"Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"


def candidate_lnks() -> list[Path]:
    """Os ScribaDev.lnk que o app cria (Área de Trabalho, Iniciar, Startup) ou que
    o usuário fixa na barra. Só os que existem."""
    from . import autostart

    dirs = [util.known_folder(_FOLDERID_DESKTOP), util.known_folder(_FOLDERID_PROGRAMS),
            autostart._startup_dir(), _pinned_taskbar_dir()]
    return [d / "ScribaDev.lnk" for d in dirs if d and (d / "ScribaDev.lnk").exists()]


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p or "")))


def icon_is_stale(target: str, icon: str, this_target: Path) -> bool:
    """(pura) O atalho é desta instalação (alvo = nosso tray) e o ícone está
    vazio ou aponta para arquivo inexistente? Atalho de outra instalação, ou
    com ícone válido, não é tocado."""
    if not target or _norm(target) != _norm(this_target):
        return False
    icon_file = str(icon or "").split(",")[0].strip()
    return not icon_file or not Path(icon_file).exists()


def read_lnks(lnks: list[Path]) -> dict[Path, tuple[str, str]]:
    """{lnk: (TargetPath, IconLocation)} via WScript.Shell, numa única chamada
    do PowerShell (saída em UTF-8: caminhos acentuados chegam inteiros)."""
    if not lnks:
        return {}
    items = ",".join(f"'{p}'" for p in lnks)
    ps = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        "$ws = New-Object -ComObject WScript.Shell; "
        f"foreach ($p in @({items})) {{ $s = $ws.CreateShortcut($p); "
        "Write-Output ($p + '|' + $s.TargetPath + '|' + $s.IconLocation) }"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out: dict[Path, tuple[str, str]] = {}
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.rstrip("\r").split("|")
        if len(parts) == 3:
            out[Path(parts[0])] = (parts[1], parts[2])
    return out


def set_lnk_icon(lnk: Path, icon: Path) -> bool:
    """Regrava só o IconLocation do .lnk (alvo, argumentos e AUMID ficam)."""
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{lnk}'); "
        f"$s.IconLocation = '{icon},0'; "
        "$s.Save()"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return proc.returncode == 0


def stale_lnks(lnks: list[Path] | None = None, target: Path | None = None,
               read=read_lnks) -> list[Path]:
    """Atalhos desta instalação com o ícone quebrado. Lista vazia fora do Windows."""
    if sys.platform != "win32":
        return []
    lnks = candidate_lnks() if lnks is None else lnks
    target = tray_exe() if target is None else target
    info = read(lnks)
    return [p for p in lnks if p in info and icon_is_stale(*info[p], target)]


def repair_stale_icons(lnks: list[Path] | None = None, target: Path | None = None,
                       icon: Path | None = None, read=read_lnks, write=set_lnk_icon) -> list[Path]:
    """Reaponta o ícone dos atalhos quebrados para o .ico atual. Devolve os
    consertados. Nunca levanta (roda no boot, em thread, best-effort)."""
    try:
        icon = util.ICON_ICO if icon is None else icon
        if not icon.exists():
            return []
        fixed = [p for p in stale_lnks(lnks, target, read) if write(p, icon)]
        if fixed:
            # o shell guarda o ícone velho em cache: sem isto a barra segue genérica
            # até o próximo logon (best-effort; ie4uinit existe desde o Vista)
            try:
                subprocess.run(["ie4uinit.exe", "-show"], capture_output=True, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                pass
        return fixed
    except Exception as e:
        print(f"aviso: reparo dos atalhos falhou ({e})")
        return []
