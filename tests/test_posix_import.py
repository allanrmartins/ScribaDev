"""#96: a cadeia de import do app não pode depender de módulos Windows-only.

Simula o ambiente Linux num subprocesso: bloqueia winreg (builtin que só existe
no Windows) e pyaudiowpatch/windows-toasts (fora do Windows nem instalam, pelos
markers do #95) e importa a cadeia completa. Se alguém reintroduzir um import
desses no topo de um módulo, este teste quebra já no CI Windows — sem esperar
o job Linux (#103).
"""

import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_SIM = """
import sys

# win32 + darwin: nenhum módulo específico de SO pode entrar no topo da cadeia
# comum de import — os backends (recorder_mac, mac_tap etc.) importam lazy (#104)
BLOCKED = {"winreg", "pyaudiowpatch", "windows_toasts",
           "sounddevice", "CoreAudio", "objc", "Foundation", "mlx_whisper"}


class LinuxSim:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"modulo bloqueado (simulando Linux): {name}")
        return None


sys.meta_path.insert(0, LinuxSim())

import scriba.main
import scriba.cli
import scriba.detector
import scriba.notify
import scriba.pipeline
import scriba.wintitles
import scriba.hotkey
import scriba.autostart
import scriba.shortcuts
import scriba.recorder
import scriba.audioprobe

print("OK")
"""


class TestImportSemModulosWindows(unittest.TestCase):
    def test_cadeia_de_import_sem_modulos_windows(self):
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", _SIM],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(REPO),
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, msg=f"stderr:\n{proc.stderr}")
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
