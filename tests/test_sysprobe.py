"""Testes da sonda de hardware + recomendação do wizard de 1º uso (#147).

`recommend()` é pura: cada perfil de máquina vira um caso determinístico.
`probe()` só tem smoke (roda sem levantar e devolve tipos sãos) — os campos
dependem da máquina.

Roda sem dependências externas:  python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import sysprobe as sp  # noqa: E402


def _p(**kw) -> sp.Probe:
    return sp.Probe(**kw)


class RecommendTests(unittest.TestCase):
    def test_gpu_nvidia_boa_vai_de_cuda_turbo(self):
        r = sp.recommend(_p(gpu_nvidia=True, vram_mb=8192, ram_gb=32, cpu_cores=16,
                            disk_free_gb=100))
        self.assertEqual(r.device, "cuda")
        self.assertEqual(r.whisper_model, "large-v3-turbo")
        self.assertTrue(r.needs_cuda_libs)
        self.assertEqual(r.diarization, "recomendada")
        self.assertTrue(any("VRAM" in x for x in r.reasons))

    def test_gpu_nvidia_fraca_cai_para_cpu(self):
        r = sp.recommend(_p(gpu_nvidia=True, vram_mb=2048, ram_gb=16, cpu_cores=8,
                            disk_free_gb=100))
        self.assertEqual(r.device, "cpu")
        self.assertEqual(r.whisper_model, "medium")
        self.assertFalse(r.needs_cuda_libs)
        self.assertTrue(any("pouca VRAM" in x for x in r.reasons))
        # com GPU o pyannote roda em CUDA de qualquer jeito: 2 GB não é "lento", é trava
        self.assertEqual(r.diarization, "desaconselhada")

    def test_gpu_de_4gb_transcreve_em_cuda_mas_vozes_ficam_desligadas(self):
        # #188: RTX 3050 Laptop de 4 GB (nvidia-smi reporta 4096) travava a
        # diarização; a mesma reunião rodou de boa no note de 6 GB
        r = sp.recommend(_p(gpu_nvidia=True, vram_mb=4096, ram_gb=32, cpu_cores=16,
                            disk_free_gb=100))
        self.assertEqual((r.device, r.whisper_model), ("cuda", "large-v3-turbo"))
        self.assertEqual(r.diarization, "desaconselhada")
        self.assertIn("4 GB de VRAM", r.diarization_hint)
        self.assertIn("não baixar os modelos", r.diarization_hint)
        self.assertTrue(any("Separação de vozes desligada" in x for x in r.reasons))

    def test_gpu_acima_de_4gb_recomenda_vozes_e_token(self):
        r = sp.recommend(_p(gpu_nvidia=True, vram_mb=6144, ram_gb=16, cpu_cores=8,
                            disk_free_gb=100))
        self.assertEqual(r.diarization, "recomendada")
        self.assertIn("6 GB de VRAM", r.diarization_hint)
        self.assertTrue(any("Hugging Face" in x for x in r.reasons))
        # a régua fica ACIMA das placas de 4 GB (que reportam ~4096) e ABAIXO das de 6 GB
        self.assertGreater(sp.DIARIZATION_MIN_VRAM_MB, 4200)
        self.assertLessEqual(sp.DIARIZATION_MIN_VRAM_MB, 6000)

    def test_apple_silicon_vai_de_mlx_turbo(self):
        r = sp.recommend(_p(apple_silicon=True, ram_gb=16, cpu_cores=8, disk_free_gb=100))
        self.assertEqual(r.device, "mlx")
        self.assertEqual(r.whisper_model, "large-v3-turbo")
        self.assertFalse(r.needs_cuda_libs)
        self.assertEqual(r.diarization, "opcional")

    def test_cpu_modesta_small_e_fraca_tiny(self):
        r = sp.recommend(_p(ram_gb=8, cpu_cores=4, disk_free_gb=50))
        self.assertEqual((r.device, r.whisper_model), ("cpu", "small"))
        r = sp.recommend(_p(ram_gb=4, cpu_cores=2, disk_free_gb=50))
        self.assertEqual((r.device, r.whisper_model), ("cpu", "tiny"))
        self.assertEqual(r.diarization, "desaconselhada")

    def test_probe_desconhecida_degrada_sem_estourar(self):
        # tudo None/False (sonda falhou em tudo): ainda sai uma recomendação válida
        r = sp.recommend(_p())
        self.assertIn(r.whisper_model, sp.MODEL_DOWNLOAD_MB)
        self.assertEqual(r.device, "cpu")
        self.assertTrue(r.reasons)

    def test_aviso_de_disco_apertado(self):
        r = sp.recommend(_p(gpu_nvidia=True, vram_mb=8192, ram_gb=32, cpu_cores=16,
                            disk_free_gb=4))
        self.assertTrue(any("disco" in x.lower() for x in r.reasons))
        # com disco folgado o aviso não aparece
        r2 = sp.recommend(_p(gpu_nvidia=True, vram_mb=8192, ram_gb=32, cpu_cores=16,
                             disk_free_gb=100))
        self.assertFalse(any("disco" in x.lower() for x in r2.reasons))


class DiagnosticoDiarizacaoTests(unittest.TestCase):
    """Avisos puros compartilhados pelo doctor, a aba Sobre e o diagnóstico (#190)."""

    def test_aviso_de_vram_segue_a_regua_do_wizard(self):
        self.assertIsNone(sp.diarization_vram_warning(None))        # VRAM desconhecida: cala
        self.assertIsNone(sp.diarization_vram_warning(6144))
        self.assertIsNone(sp.diarization_vram_warning(8192))
        aviso = sp.diarization_vram_warning(4096)
        self.assertIn("4 GB de VRAM", aviso)
        self.assertIn("TRAVAR", aviso)
        self.assertIn("aba Gravação", aviso)

    def test_torch_build_cpu_com_gpu_nvidia_avisa_com_o_comando(self):
        aviso = sp.torch_cpu_build_warning("2.12.1+cpu", gpu_nvidia=True)
        self.assertIn("build CPU", aviso)
        self.assertIn(sp.TORCH_CUDA_FIX, aviso)
        # com CUDA disponível em runtime também: o sufixo manda (é build CPU mesmo)
        self.assertIsNotNone(sp.torch_cpu_build_warning("2.12.1+cpu", True, cuda_available=True))

    def test_torch_cuda_indisponivel_em_runtime_tambem_avisa(self):
        aviso = sp.torch_cpu_build_warning("2.12.1+cu128", True, cuda_available=False)
        self.assertIn("não enxerga a GPU", aviso)
        self.assertIn(sp.TORCH_CUDA_FIX, aviso)

    def test_torch_sem_motivo_de_aviso_cala(self):
        self.assertIsNone(sp.torch_cpu_build_warning("2.12.1+cu128", True))
        self.assertIsNone(sp.torch_cpu_build_warning("2.12.1+cu128", True, cuda_available=True))
        self.assertIsNone(sp.torch_cpu_build_warning("2.12.1+cpu", gpu_nvidia=False))  # sem GPU: ok
        self.assertIsNone(sp.torch_cpu_build_warning(None, True))                     # sem torch
        self.assertIsNone(sp.torch_cpu_build_warning("", True))


class ProbeSmokeTests(unittest.TestCase):
    def test_probe_nao_levanta_e_tipos_sao_saos(self):
        p = sp.probe()
        self.assertIsInstance(p.gpu_nvidia, bool)
        self.assertIsInstance(p.apple_silicon, bool)
        if p.cpu_cores is not None:
            self.assertGreater(p.cpu_cores, 0)
        if p.ram_gb is not None:
            self.assertGreater(p.ram_gb, 0)
        if p.vram_mb is not None:
            self.assertGreater(p.vram_mb, 0)
        # a recomendação da máquina real também sai sem estourar
        r = sp.recommend(p)
        self.assertIn(r.device, ("cuda", "mlx", "cpu"))


if __name__ == "__main__":
    unittest.main()
