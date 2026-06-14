"""Atalhos do ScribaDev (.lnk) na Área de Trabalho e no menu Iniciar.

Apontam para o scribadev-tray.exe (sem console) e usam o ícone do app. Sem admin:
tudo no perfil do usuário, criado via WScript.Shell (mesma abordagem do autostart).
Cada .lnk também recebe a propriedade System.AppUserModel.ID = ScribaDev.App: é ela
que faz "Fixar na barra de tarefas" agrupar com a janela do app (que declara o
mesmo AUMID) em vez de cair no ícone/hint do Python.
"""

from __future__ import annotations

import ctypes
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


def _com_method(obj: c_void_p, index: int, *argtypes, restype=ctypes.HRESULT):
    """Resolve o método `index` da vtable de `obj` (COM cru, sem comtypes)."""
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
