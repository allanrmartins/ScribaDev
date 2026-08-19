"""Componentes sob demanda (#154): plano, worker compartilhado (wizard/Configurações)
e medição de cache. Downloads/pips mockados — determinístico, sem rede."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import components, sysprobe  # noqa: E402


class PlanTests(unittest.TestCase):
    def test_plan_completo_e_pesos(self):
        items = {k: mb for k, _l, mb in components.plan_items("small", cuda=True, voices=True)}
        self.assertEqual(items["model:small"], sysprobe.MODEL_DOWNLOAD_MB["small"])
        self.assertEqual(items["cuda"], components.CUDA_MB)
        self.assertEqual(items["voices"], components.VOICES_MB)

    def test_plan_vazio_sem_selecao(self):
        self.assertEqual(components.plan_items(), [])

    def test_plan_parcial(self):
        self.assertEqual([k for k, _l, _mb in components.plan_items(voices=True)], ["voices"])
        self.assertEqual([k for k, _l, _mb in components.plan_items("base")], ["model:base"])


class RunTests(unittest.TestCase):
    def test_sucesso_agrega_e_barra_chega_a_1000(self):
        fracs, lines = [], []
        with mock.patch.object(components, "_download_model", return_value=(True, "")) as dm, \
                mock.patch.object(components, "_install_extras", return_value=(True, "")) as ie:
            ok, notes = components.run(components.plan_items("small", voices=True),
                                       progress=lines.append, frac=fracs.append, poll=False)
        self.assertTrue(ok)
        self.assertEqual(notes, "")
        self.assertEqual(dm.call_args[0][0], "small")
        self.assertEqual(ie.call_args[0][0], components.VOICES_PKGS)
        self.assertEqual(fracs[-1], 1000)
        self.assertEqual(fracs, sorted(fracs))   # a barra nunca anda para trás

    def test_falha_de_um_item_nao_derruba_o_resto(self):
        lines = []
        with mock.patch.object(components, "_download_model", return_value=(False, "modelo: x")), \
                mock.patch.object(components, "_install_extras", return_value=(True, "")) as ie:
            ok, notes = components.run(components.plan_items("small", voices=True),
                                       progress=lines.append, frac=lambda _f: None, poll=False)
        self.assertFalse(ok)
        self.assertIn("modelo: x", notes)
        ie.assert_called_once()                  # as vozes seguiram apesar do modelo falhar
        self.assertTrue(any("instalados" in l for l in lines))

    def test_excecao_num_item_nao_mata_os_demais(self):
        """Regressão do worker antigo do wizard: exceção no item do MODELO abortava o
        plano inteiro — o usuário ficava sem CUDA/vozes sem nenhuma pista do porquê."""
        with mock.patch.object(components, "_download_model", side_effect=RuntimeError("boom")), \
                mock.patch.object(components, "_install_extras", return_value=(True, "")) as ie:
            ok, notes = components.run(components.plan_items("small", voices=True),
                                       progress=lambda _l: None, frac=lambda _f: None, poll=False)
        self.assertFalse(ok)
        self.assertIn("boom", notes)
        ie.assert_called_once()   # as vozes rodaram mesmo com o modelo explodindo

    def test_excecao_vira_nota_nunca_levanta(self):
        with mock.patch.object(components, "_install_extras", side_effect=RuntimeError("rede caiu")):
            ok, notes = components.run(components.plan_items(voices=True),
                                       progress=lambda _l: None, frac=lambda _f: None, poll=False)
        self.assertFalse(ok)
        self.assertIn("rede caiu", notes)

    def test_cuda_usa_os_pacotes_nvidia(self):
        with mock.patch.object(components, "_install_extras", return_value=(True, "")) as ie:
            ok, _ = components.run(components.plan_items(cuda=True),
                                   progress=lambda _l: None, frac=lambda _f: None, poll=False)
        self.assertTrue(ok)
        self.assertEqual(ie.call_args[0][0], components.CUDA_PKGS)


class CliComponentsTests(unittest.TestCase):
    """`scribadev components` (#164): retry dos downloads por terminal, sem GUI."""

    def test_roda_o_plano_selecionado(self):
        from scriba import cli
        with mock.patch.object(components, "run", return_value=(True, "")) as r:
            rc = cli.main(["components", "--voices"])
        self.assertEqual(rc, 0)
        self.assertEqual([k for k, _l, _mb in r.call_args[0][0]], ["voices"])

    def test_sem_selecao_e_erro_de_uso(self):
        from scriba import cli
        with mock.patch.object(components, "run") as r:
            rc = cli.main(["components"])
        self.assertEqual(rc, 2)
        r.assert_not_called()

    def test_falha_vira_exit_code_1(self):
        from scriba import cli
        with mock.patch.object(components, "run", return_value=(False, "pip retornou 2")):
            rc = cli.main(["components", "--cuda"])
        self.assertEqual(rc, 1)


class DirMbTests(unittest.TestCase):
    def test_dir_mb_mede_e_degrada(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_comp_")) / "cache"
        d.mkdir(parents=True)
        (d / "blob.bin").write_bytes(b"x" * 1048576)   # 1 MB
        self.assertAlmostEqual(components.dir_mb(d), 1.0, places=2)
        self.assertEqual(components.dir_mb(d / "nao-existe"), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
