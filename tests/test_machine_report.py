"""Foto da máquina no diagnóstico e no watchdog (#182/#183).

Dois usuários travando a transcrição CUDA e o zip dizia só "GPU NVIDIA: sim" -
sem modelo, driver, carga, VRAM nem quem estava usando a placa. Aqui: as sondas
novas do sysprobe (com o nvidia-smi mockado - o runner do CI não tem GPU), o
maquina.txt do diagnóstico e a linha compacta que o watchdog loga no instante
em que declara um subprocesso travado.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import diagnostics as dg  # noqa: E402
from scriba import sysprobe, util  # noqa: E402

_GPU_CSV = [["NVIDIA GeForce RTX 3070 Ti", "596.36", "8192", "1839", "6179", "23", "49"]]
# WDDM devolve TAMBÉM processos só-gráficos, com used_memory [N/A]: navegador,
# Teams, explorer - dezenas de linhas que são ruído e expõem os apps do usuário
_APPS_CSV = [
    ["12345", r"C:\Users\fulano\AppData\Local\ScribaDev\scribadev.exe", "1500"],
    ["29376", r"C:\Users\fulano\AppData\Local\Google\Chrome\Application\chrome.exe", "[N/A]"],
    ["21468", r"C:\Program Files\WindowsApps\MSTeams\ms-teams.exe", "[N/A]"],
]


def _smi(respostas):
    """Substitui sysprobe._nvidia_smi: devolve por prefixo da consulta."""
    def fake(args):
        chave = "gpu" if args[0].startswith("--query-gpu") else "apps"
        return respostas.get(chave)
    return fake


class GpuSnapshotTests(unittest.TestCase):
    def test_identidade_memoria_e_carga(self):
        with mock.patch.object(sysprobe, "_nvidia_smi",
                               _smi({"gpu": _GPU_CSV, "apps": []})):
            gs = sysprobe.gpu_snapshot()
        self.assertEqual(gs["name"], "NVIDIA GeForce RTX 3070 Ti")
        self.assertEqual(gs["driver"], "596.36")
        self.assertEqual(gs["vram_total_mb"], 8192)
        self.assertEqual(gs["vram_used_mb"], 1839)
        self.assertEqual(gs["util_pct"], 23)
        self.assertEqual(gs["temp_c"], 49)

    def test_processo_grafico_com_na_fica_de_fora(self):
        """Só quem tem used_memory numérico é compute de verdade; o resto é o
        ruído do WDDM - e vazaria os apps do usuário no zip."""
        with mock.patch.object(sysprobe, "_nvidia_smi",
                               _smi({"gpu": _GPU_CSV, "apps": _APPS_CSV})):
            gs = sysprobe.gpu_snapshot()
        self.assertEqual(len(gs["procs"]), 1)
        self.assertEqual(gs["procs"][0]["pid"], 12345)
        self.assertEqual(gs["procs"][0]["mem_mb"], 1500)

    def test_nome_do_processo_e_so_o_basename(self):
        with mock.patch.object(sysprobe, "_nvidia_smi",
                               _smi({"gpu": _GPU_CSV, "apps": _APPS_CSV})):
            gs = sysprobe.gpu_snapshot()
        self.assertEqual(gs["procs"][0]["name"], "scribadev.exe")

    def test_sem_nvidia_smi_devolve_none(self):
        with mock.patch.object(sysprobe, "_nvidia_smi", lambda args: None):
            self.assertIsNone(sysprobe.gpu_snapshot())


class RamSnapshotTests(unittest.TestCase):
    def test_shape_no_windows(self):
        if sys.platform != "win32":
            self.skipTest("GlobalMemoryStatusEx é do Windows")
        ram = sysprobe.ram_snapshot()
        self.assertIsNotNone(ram)
        for campo in ("total_gb", "avail_gb", "load_pct",
                      "pagefile_total_gb", "pagefile_avail_gb"):
            self.assertIn(campo, ram)
        self.assertGreater(ram["total_gb"], 0)
        self.assertLessEqual(ram["avail_gb"], ram["total_gb"])


class SnapshotLineTests(unittest.TestCase):
    """A linha que o watchdog loga ao declarar TRAVADO: a única foto do instante
    real do problema - o Diagnóstico roda depois, com a carga já outra."""

    def test_uma_linha_com_gpu_e_ram(self):
        with mock.patch.object(sysprobe, "_nvidia_smi",
                               _smi({"gpu": _GPU_CSV, "apps": _APPS_CSV})):
            linha = sysprobe.snapshot_line()
        self.assertNotIn("\n", linha)
        self.assertIn("RTX 3070 Ti", linha)
        self.assertIn("driver 596.36", linha)
        self.assertIn("1839/8192 MB", linha)
        self.assertIn("scribadev.exe", linha)
        self.assertNotIn("chrome", linha)   # o ruído do WDDM não entra nem aqui

    def test_sem_gpu_ainda_devolve_texto(self):
        with mock.patch.object(sysprobe, "_nvidia_smi", lambda args: None):
            linha = sysprobe.snapshot_line()
        self.assertIn("nvidia-smi indisponivel", linha)

    def test_nunca_levanta(self):
        with mock.patch.object(sysprobe, "gpu_snapshot",
                               side_effect=RuntimeError("boom")):
            try:
                sysprobe.snapshot_line()
            except RuntimeError:
                self.fail("snapshot_line não pode derrubar o watchdog")


class MachineReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = util.APP_DIR
        util.APP_DIR = Path(self._tmp.name)
        (util.APP_DIR / "logs").mkdir()
        (util.APP_DIR / "config.toml").write_text("[detection]\n", encoding="utf-8")
        addons = util.APP_DIR / "addons"
        (addons / "nvidia_cudnn_cu12-9.24.0.43.dist-info").mkdir(parents=True)
        (addons / "torch-2.13.0.dist-info").mkdir()

    def tearDown(self):
        util.APP_DIR = self._orig
        self._tmp.cleanup()

    def test_relatorio_traz_todas_as_secoes(self):
        with mock.patch.object(sysprobe, "_nvidia_smi",
                               _smi({"gpu": _GPU_CSV, "apps": _APPS_CSV})):
            rel = dg.machine_report()
        for secao in ("== GPU (nvidia-smi) ==", "== RAM ==", "== Disco ==",
                      "== APP_DIR", "== Componentes instalados (addons) ==",
                      "== Modelos (cache Hugging Face) =="):
            self.assertIn(secao, rel)

    def test_versoes_do_addons_saem_do_dist_info(self):
        """Na análise do #182 as versões tiveram que ser garimpadas do pip.log -
        e a diferença de UMA versão de cudnn era a pista central."""
        with mock.patch.object(sysprobe, "_nvidia_smi", lambda args: None):
            rel = dg.machine_report()
        self.assertIn("nvidia_cudnn_cu12", rel)
        self.assertIn("9.24.0.43", rel)
        self.assertIn("torch", rel)
        self.assertIn("2.13.0", rel)

    def test_sem_gpu_o_relatorio_sai_dizendo_isso(self):
        with mock.patch.object(sysprobe, "_nvidia_smi", lambda args: None):
            rel = dg.machine_report()
        self.assertIn("nvidia-smi indisponivel", rel)

    def test_appdir_lista_conteudo_com_tamanho(self):
        with mock.patch.object(sysprobe, "_nvidia_smi", lambda args: None):
            rel = dg.machine_report()
        self.assertIn("config.toml", rel)
        self.assertIn("addons/", rel)

    def test_nunca_levanta_mesmo_sem_nada(self):
        util.APP_DIR = Path(self._tmp.name) / "nao-existe"
        with mock.patch.object(sysprobe, "_nvidia_smi", lambda args: None):
            rel = dg.machine_report()   # não pode explodir
        self.assertIn("== GPU", rel)


class EnvironmentReportTests(unittest.TestCase):
    def test_gpu_com_modelo_e_driver(self):
        with mock.patch.object(sysprobe, "_nvidia_smi",
                               _smi({"gpu": _GPU_CSV, "apps": []})):
            env = dg.environment_report()
        self.assertIn("RTX 3070 Ti", env)
        self.assertIn("driver 596.36", env)
        self.assertIn("8192 MB VRAM", env)
        self.assertIn("CPU", env)
        self.assertIn("RAM", env)


if __name__ == "__main__":
    unittest.main(verbosity=2)
