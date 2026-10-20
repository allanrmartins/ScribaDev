"""Lançador do modo código-fonte no macOS (installer/macos/make_source_app.py).

Sem o .app, rodando cru do venv, o macOS nega o áudio do sistema em silêncio: o
bundle precisa das MESMAS frases de uso do app do DMG (fonte única bundle_info.py).
"""

import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "installer" / "macos"))


@unittest.skipUnless(sys.platform == "darwin", "usa clang/sips/iconutil/codesign do macOS")
class MakeSourceAppTests(unittest.TestCase):
    def _venv(self, tmp: Path) -> Path:
        """O venv deste processo, se houver; senão um venv novo que enxerga o do sistema."""
        if sys.prefix != sys.base_prefix:
            return Path(sys.prefix)
        venv = tmp / "venv"
        subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)], check=True)
        return venv

    def test_bundle_completo_assinado_e_hospedando_o_python(self):
        import json
        import os

        import make_source_app
        from bundle_info import PERMISSION_PLIST

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            app = make_source_app.build(self._venv(tmp), tmp / "Apps" / "ScribaDev Dev.app")
            info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
            for k, v in PERMISSION_PLIST.items():
                self.assertEqual(info[k], v)
            self.assertEqual(info["CFBundleIdentifier"], "dev.scribadev.source")
            self.assertTrue((app / "Contents" / "Resources" / "scriba.icns").stat().st_size > 0)
            subprocess.run(["codesign", "--verify", str(app)], check=True)
            exe = app / "Contents" / "MacOS" / "ScribaDev"
            # nativo (Mach-O), não script: é ELE que hospeda o Python - a Acessibilidade
            # é checada contra este executável, e não contra o Python.app
            self.assertEqual(exe.read_bytes()[:4], bytes.fromhex("cffaedfe"))
            env = {**os.environ, "PYTHONPATH": str(REPO)}
            out = subprocess.run([str(exe), "--launcher-check"], check=True, capture_output=True,
                                 text=True, env=env, timeout=60).stdout
            check = json.loads(out.strip().splitlines()[-1])
            self.assertEqual(Path(check["bundle"]).resolve(), app.resolve())
            self.assertTrue(check["scriba"].endswith("scriba/__init__.py"))


if __name__ == "__main__":
    unittest.main()
