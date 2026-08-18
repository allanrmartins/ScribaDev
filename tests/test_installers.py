"""Invariantes dos .spec do PyInstaller (Windows e macOS) — épico #138.

Os .spec só rodam DENTRO do PyInstaller (usam `SPECPATH` e hooks), então aqui a
verificação é sobre o texto: o que quebrou no DMG 1.4.3 foi exatamente o que um
grep pega — um recurso de runtime que não entrou nos `datas` e uma chave do
Info.plist que o PyInstaller preenche sozinho com o valor errado.
"""

import plistlib
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAC_SPEC = REPO / "installer" / "macos" / "scribadev-mac.spec"
WIN_SPEC = REPO / "installer" / "windows" / "scribadev.spec"


class DatasDoPacoteTests(unittest.TestCase):
    """Todo diretório de recursos do pacote precisa estar nos `datas` dos DOIS specs.

    Regressão: `scriba/qt/icons` (SVGs Fluent lidos por theme.icon) ficou de fora
    até a 1.4.3 e o app instalado abriu sem ícone nenhum na UI.
    """

    # dirs de recurso = os que têm arquivo não-.py e são lidos por util.resource_path
    RECURSOS = ("scriba/assets", "scriba/qt/icons")

    def test_specs_empacotam_todos_os_recursos(self):
        for spec in (MAC_SPEC, WIN_SPEC):
            txt = spec.read_text(encoding="utf-8")
            for rec in self.RECURSOS:
                with self.subTest(spec=spec.name, recurso=rec):
                    self.assertIn(f'"{rec}"', txt, f"{spec.name} não empacota {rec}")

    def test_nenhum_dir_de_recurso_ficou_de_fora_da_lista(self):
        """Se alguém adicionar um novo dir de recursos no pacote, este teste avisa."""
        achados = {
            f"scriba/{p.parent.relative_to(REPO / 'scriba').as_posix()}"
            for p in (REPO / "scriba").rglob("*")
            if p.is_file() and p.suffix != ".py" and "__pycache__" not in p.parts
        }
        self.assertEqual(achados, set(self.RECURSOS),
                         "dir de recursos novo/removido — atualize RECURSOS e os .spec")


class InfoPlistTests(unittest.TestCase):
    def test_lsbackgroundonly_desligado_explicitamente(self):
        """O BUNDLE do PyInstaller herda `console` do ÚLTIMO EXE do COLLECT (a CLI) e
        grava LSBackgroundOnly=True — um app assim não pode ser ativado pelo macOS:
        as janelas abrem, mas o foco e o teclado ficam no app anterior (DMG 1.4.3)."""
        txt = MAC_SPEC.read_text(encoding="utf-8")
        self.assertRegex(txt, r'"LSBackgroundOnly":\s*False')

    def test_usage_strings_de_permissao_presentes(self):
        txt = MAC_SPEC.read_text(encoding="utf-8")
        for chave in ("NSMicrophoneUsageDescription", "NSAudioCaptureUsageDescription"):
            with self.subTest(chave=chave):
                self.assertIn(chave, txt)

    @unittest.skipUnless(sys.platform == "darwin", "só faz sentido no macOS")
    def test_app_instalado_nao_e_background_only(self):
        """Se houver um .app construído em installer/macos/dist, confere o resultado."""
        plist = REPO / "installer" / "macos" / "dist" / "ScribaDev.app" / "Contents" / "Info.plist"
        if not plist.exists():
            self.skipTest("nenhum .app construído")
        data = plistlib.loads(plist.read_bytes())
        self.assertFalse(data.get("LSBackgroundOnly", False))
        self.assertEqual(data["CFBundleExecutable"], "ScribaDevApp")


class DylibsDoMlxTests(unittest.TestCase):
    def test_spec_coleta_os_dylibs_do_mlx(self):
        """O scanner do PyInstaller perde o libjaccl.dylib (dep @rpath do libmlx) e o
        import do mlx_whisper morre num dlopen dentro do .app — a coleta é explícita."""
        txt = MAC_SPEC.read_text(encoding="utf-8")
        self.assertIn("collect_dynamic_libs", txt)
        self.assertIn('collect_dynamic_libs("mlx")', txt)
        self.assertIn("_mlx_binaries", txt)

    @unittest.skipUnless(sys.platform == "darwin", "só faz sentido no macOS")
    def test_app_construido_tem_os_dois_dylibs(self):
        fw = REPO / "installer" / "macos" / "dist" / "ScribaDev.app" / "Contents" / "Frameworks"
        if not fw.is_dir():
            self.skipTest("nenhum .app construído")
        if not (fw / "mlx").is_dir():
            self.skipTest("build sem mlx (não-arm64)")
        for lib in ("libmlx.dylib", "libjaccl.dylib"):
            with self.subTest(lib=lib):
                # a raiz do bundle é onde os rpaths do libmlx procuram
                self.assertTrue((fw / lib).exists(), f"{lib} não foi para o bundle")


class DadosDeTerceirosTests(unittest.TestCase):
    def test_specs_coletam_o_onnx_do_faster_whisper(self):
        """O transcriber roda SEMPRE com vad_filter=True: sem o silero_vad_v6.onnx a
        transcrição morre com "NO_SUCHFILE" dentro do bundle (nos dois SOs)."""
        for spec in (MAC_SPEC, WIN_SPEC):
            with self.subTest(spec=spec.name):
                txt = spec.read_text(encoding="utf-8")
                self.assertIn('collect_data_files("faster_whisper"', txt)
                self.assertIn("_fw_datas", txt)

    def test_spec_mac_coleta_os_assets_do_mlx_whisper(self):
        """mel_filters.npz + .tiktoken: sem eles o modelo carrega "em metal" e a 1ª
        transcrição morre em "[load_npz] Input must be a zip file..."."""
        txt = MAC_SPEC.read_text(encoding="utf-8")
        self.assertIn('collect_all("mlx_whisper")', txt)
        self.assertIn("_mlxw_datas", txt)

    def test_spec_mac_coleta_o_mlx_inteiro(self):
        """mlx precisa dos submódulos puros que o core.so importa em runtime
        (mlx._reprlib_fix), do metallib e do libjaccl na raiz — collect_all cobre os
        dois primeiros; sem isso o app instalado nunca usa Metal."""
        txt = MAC_SPEC.read_text(encoding="utf-8")
        self.assertIn('collect_all("mlx")', txt)
        self.assertIn("_mlx_hidden", txt)
        self.assertIn('collect_dynamic_libs("mlx")', txt)
        self.assertIn("metallib", txt)


class EntryPointsTests(unittest.TestCase):
    ENTRIES = (
        REPO / "installer" / "macos" / "entry_cli.py",
        REPO / "installer" / "macos" / "entry_tray.py",
        REPO / "installer" / "windows" / "entry_cli.py",
        REPO / "installer" / "windows" / "entry_tray.py",
    )

    def test_todos_chamam_freeze_support(self):
        """Sem `multiprocessing.freeze_support()` o `sys.executable -c "from
        multiprocessing.resource_tracker import main..."` do CPython cai no argparse
        do app e o processo auxiliar morre (o PyInstaller desvia dentro dessa função)."""
        for arq in self.ENTRIES:
            with self.subTest(entry=arq.parent.name + "/" + arq.name):
                txt = arq.read_text(encoding="utf-8")
                self.assertIn("multiprocessing.freeze_support()", txt)
                self.assertLess(txt.index("multiprocessing.freeze_support()"),
                                txt.index("sys.exit("), "freeze_support depois do main")

    def test_entries_compilam(self):
        for arq in self.ENTRIES:
            with self.subTest(entry=arq.name):
                compile(arq.read_text(encoding="utf-8"), str(arq), "exec")


class SintaxeTests(unittest.TestCase):
    def test_specs_compilam(self):
        """Erro de sintaxe num .spec só aparece na hora do build (que é caro)."""
        for spec in (MAC_SPEC, WIN_SPEC):
            with self.subTest(spec=spec.name):
                compile(spec.read_text(encoding="utf-8"), str(spec), "exec")


class VersaoTests(unittest.TestCase):
    def test_versao_do_bundle_sai_do_pacote(self):
        """O .spec e o build.sh leem __version__ de scriba/__init__.py (fonte única)."""
        ver = re.search(r'__version__\s*=\s*"([^"]+)"',
                        (REPO / "scriba" / "__init__.py").read_text(encoding="utf-8"))
        self.assertIsNotNone(ver)
        self.assertRegex(ver.group(1), r"^\d+\.\d+\.\d+")
        for arq in (MAC_SPEC, REPO / "installer" / "macos" / "build.sh"):
            with self.subTest(arq=arq.name):
                self.assertIn("__version__", arq.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
