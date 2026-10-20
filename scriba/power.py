"""Fonte de energia do computador: está na bateria ou na tomada?

Serve à regra "separar vozes só na tomada" (diarization.only_on_ac): a separação
de vozes é a etapa mais pesada do processamento (GPU a ~95 °C por ~2 min num
MacBook Air sem ventoinha) e, na bateria, o usuário prefere a nota já e as vozes
depois.

Contrato: `on_battery()` só devolve True com CERTEZA de estar na bateria. Desktop
(sem bateria), sonda indisponível ou qualquer erro = False - na dúvida, nunca
adiar o que o usuário pediu.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger("scriba.power")


def _parse_pmset(out: str) -> bool:
    """`pmset -g ps` → 1ª linha "Now drawing from 'Battery Power'" (ou 'AC Power',
    'UPS Power'). Só 'Battery Power' conta como bateria."""
    first = out.strip().splitlines()[0] if out.strip() else ""
    return "'Battery Power'" in first


def _mac_on_battery() -> bool:
    import subprocess

    out = subprocess.run(["/usr/bin/pmset", "-g", "ps"], capture_output=True, text=True, timeout=5)
    return out.returncode == 0 and _parse_pmset(out.stdout)


def _win_on_battery() -> bool:
    import ctypes
    from ctypes import wintypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", wintypes.BYTE), ("BatteryFlag", wintypes.BYTE),
                    ("BatteryLifePercent", wintypes.BYTE), ("SystemStatusFlag", wintypes.BYTE),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]

    st = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
        return False
    # ACLineStatus: 0 = bateria, 1 = tomada, 255 = desconhecido. BatteryFlag 128 = sem bateria
    return st.ACLineStatus == 0 and st.BatteryFlag != 128


def _linux_on_battery(root: Path = Path("/sys/class/power_supply")) -> bool:
    """Bateria descarregando e nenhuma fonte externa (Mains/USB) online."""
    discharging = False
    for d in root.iterdir() if root.is_dir() else ():
        try:
            kind = (d / "type").read_text().strip()
            if kind in ("Mains", "USB") and (d / "online").read_text().strip() == "1":
                return False
            if kind == "Battery" and (d / "status").read_text().strip() == "Discharging":
                discharging = True
        except OSError:
            continue
    return discharging


def on_battery() -> bool:
    """True só com certeza de estar na bateria (ver o contrato no topo)."""
    try:
        if sys.platform == "darwin":
            return _mac_on_battery()
        if sys.platform == "win32":
            return _win_on_battery()
        return _linux_on_battery()
    except Exception as e:
        log.debug("sonda de energia indisponível (%s) - tratando como tomada", e)
        return False
