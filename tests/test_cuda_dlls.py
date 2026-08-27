"""As DLLs CUDA do addons entram no caminho de busca no app CONGELADO (#185).

`bootstrap_cuda_dlls` procurava os pacotes nvidia-* só em
`sys.prefix/Lib/site-packages/nvidia`. Isso vale para instalação por fonte, onde
`sys.prefix` é o venv - mas no exe do PyInstaller `sys.prefix` é o `_MEIPASS`, e
lá não existe `Lib/site-packages`. Como os componentes pesados do congelado são
baixados para `APP_DIR/addons`, o cuBLAS/cuDNN que o usuário baixou NUNCA entrava
no PATH do processo: o ctranslate2 carrega essas DLLs com LoadLibrary, que
consulta o PATH, e o `addons.bootstrap()` só mexe no `sys.path` (import de
Python, não carga de DLL).

Efeito de campo: transcrição CUDA parava na primeira operação, sem erro e sem
consumir CPU, em toda reunião de quem usa o instalador (#182, #183).
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import util  # noqa: E402


class CudaDllDirsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self._tmp.name)
        self._app0 = util.APP_DIR
        util.APP_DIR = self.raiz / "appdir"

    def tearDown(self):
        util.APP_DIR = self._app0
        self._tmp.cleanup()

    def _cria_nvidia(self, base: Path, pacotes=("cublas", "cudnn", "cuda_nvrtc")):
        for p in pacotes:
            (base / p / "bin").mkdir(parents=True)

    def test_acha_no_addons_do_congelado(self):
        """O caso que estava quebrado: exe do instalador, componentes no addons."""
        self._cria_nvidia(util.APP_DIR / "addons" / "nvidia")

        bins = util.cuda_dll_dirs()

        self.assertEqual(len(bins), 3)
        for b in bins:
            self.assertTrue(b.endswith("bin"))
            self.assertIn("addons", b)

    def test_acha_no_venv_da_instalacao_por_fonte(self):
        prefixo = self.raiz / "venv"
        self._cria_nvidia(prefixo / "Lib" / "site-packages" / "nvidia")
        orig = sys.prefix
        try:
            sys.prefix = str(prefixo)
            bins = util.cuda_dll_dirs()
        finally:
            sys.prefix = orig

        self.assertEqual(len(bins), 3)
        for b in bins:
            self.assertIn("site-packages", b)

    def test_soma_os_dois_layouts(self):
        """Sem preferência entre eles: quem tiver DLL entra no caminho."""
        prefixo = self.raiz / "venv"
        self._cria_nvidia(prefixo / "Lib" / "site-packages" / "nvidia", ("cublas",))
        self._cria_nvidia(util.APP_DIR / "addons" / "nvidia", ("cudnn",))
        orig = sys.prefix
        try:
            sys.prefix = str(prefixo)
            bins = util.cuda_dll_dirs()
        finally:
            sys.prefix = orig

        self.assertEqual(len(bins), 2)
        self.assertTrue(any("cublas" in b for b in bins))
        self.assertTrue(any("cudnn" in b for b in bins))

    def test_sem_nada_devolve_lista_vazia(self):
        self.assertEqual(util.cuda_dll_dirs(), [])

    def test_appdir_inexistente_nao_levanta(self):
        util.APP_DIR = self.raiz / "nao-existe"
        self.assertEqual(util.cuda_dll_dirs(), [])

    def test_pasta_sem_bin_nao_entra(self):
        """dist-info e pacote sem DLL não viram entrada de PATH."""
        base = util.APP_DIR / "addons" / "nvidia"
        (base / "cublas" / "bin").mkdir(parents=True)
        (base / "cudnn" / "lib").mkdir(parents=True)

        bins = util.cuda_dll_dirs()

        self.assertEqual(len(bins), 1)
        self.assertIn("cublas", bins[0])


class BootstrapCudaDllsTests(unittest.TestCase):
    """O bootstrap põe no PATH do processo - é o que o LoadLibrary consulta."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self._tmp.name)
        self._app0 = util.APP_DIR
        self._flag0 = util._dll_dirs_added
        util.APP_DIR = self.raiz / "appdir"
        util._dll_dirs_added = False

    def tearDown(self):
        util.APP_DIR = self._app0
        util._dll_dirs_added = self._flag0
        self._tmp.cleanup()

    @unittest.skipUnless(sys.platform == "win32", "add_dll_directory é do Windows")
    def test_addons_entra_no_path_do_processo(self):
        import os

        (util.APP_DIR / "addons" / "nvidia" / "cublas" / "bin").mkdir(parents=True)
        path0 = os.environ.get("PATH", "")
        try:
            util.bootstrap_cuda_dlls()
            self.assertIn(str(util.APP_DIR / "addons" / "nvidia" / "cublas" / "bin"),
                          os.environ["PATH"])
        finally:
            os.environ["PATH"] = path0

    def test_roda_uma_vez_so(self):
        (util.APP_DIR / "addons" / "nvidia" / "cublas" / "bin").mkdir(parents=True)
        util.bootstrap_cuda_dlls()
        self.assertTrue(util._dll_dirs_added)
        util.bootstrap_cuda_dlls()   # idempotente, não pode levantar

    def test_sem_dll_nenhuma_nao_levanta(self):
        util.bootstrap_cuda_dlls()


if __name__ == "__main__":
    unittest.main(verbosity=2)
