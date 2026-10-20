"""Lançador do modo código-fonte no macOS (installer/macos/make_source_app.py).

Sem o .app, rodando cru do venv, o macOS nega o áudio do sistema em silêncio: o
bundle precisa das MESMAS frases de uso do app do DMG (fonte única bundle_info.py).
"""

import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "installer" / "macos"))


@unittest.skipUnless(sys.platform == "darwin", "usa sips/iconutil/codesign do macOS")
class MakeSourceAppTests(unittest.TestCase):
    def test_bundle_completo_e_assinado(self):
        import subprocess

        import make_source_app
        from bundle_info import PERMISSION_PLIST

        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "venv"
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "python3").symlink_to(sys.executable)
            app = make_source_app.build(venv, Path(tmp) / "Apps" / "ScribaDev Dev.app")
            info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
            for k, v in PERMISSION_PLIST.items():
                self.assertEqual(info[k], v)
            self.assertEqual(info["CFBundleIdentifier"], "dev.scribadev.source")
            self.assertTrue((app / "Contents" / "Resources" / "scriba.icns").stat().st_size > 0)
            exe = app / "Contents" / "MacOS" / "ScribaDev"
            texto = exe.read_text(encoding="utf-8")
            self.assertIn("SCRIBADEV_APP_BUNDLE", texto)
            self.assertIn(str(venv / "bin" / "python3"), texto)
            subprocess.run(["bash", "-n", str(exe)], check=True)
            subprocess.run(["codesign", "--verify", str(app)], check=True)


if __name__ == "__main__":
    unittest.main()
