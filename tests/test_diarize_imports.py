"""Falha ao importar os componentes de voz (torch/pyannote) — issues #196/#197.

Dois relatos de campo com a mesma causa (um módulo que o torch/pyannote importa
em runtime fora do bundle congelado) e sintomas diferentes:
- #197 (mac): "No module named 'unittest.mock'" mascarado como "falta o extra
  [diarization]" — mandava o usuário rodar um pip install que não resolve nada;
- #196 (Windows): o 1º `import torch` falhava dentro de um `except: pass` e o 2º
  morria no falso "partially initialized module 'torch' has no attribute
  'autograd' (most likely due to a circular import)".
Estes testes reproduzem a mecânica do Python por trás do #196 com um pacote
sintético, travam a cura (purge dos submódulos órfãos) e a mensagem certa.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import diarize  # noqa: E402
from scriba.config import Diarization, Whisper  # noqa: E402
from scriba.transcriber import Transcriber  # noqa: E402

PKG = "p196_fake"  # pacote sintético que imita o torch


def _limpa_modulos(prefixo: str) -> None:
    for m in [m for m in sys.modules if m == prefixo or m.startswith(prefixo + ".")]:
        del sys.modules[m]


class FalsoCircularImportTests(unittest.TestCase):
    """Reproduz a #196 em miniatura e prova que o purge devolve a causa real."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        raiz = Path(self._tmp.name) / PKG
        raiz.mkdir()
        # __init__ amarra os submódulos num único `from pkg import (...)`, como o torch
        (raiz / "__init__.py").write_text(textwrap.dedent(f"""
            from {PKG} import autograd as autograd, nested as nested
        """), encoding="utf-8")
        (raiz / "autograd.py").write_text("class Function:\n    pass\n", encoding="utf-8")
        # nested usa pkg.autograd ANTES de importar a dependência que falta — como o
        # torch.nested._internal.nested_tensor (linha 469 do traceback do relato)
        (raiz / "nested.py").write_text(textwrap.dedent(f"""
            import {PKG}

            class ViewBufferFromNested({PKG}.autograd.Function):
                pass

            import modulo_de_stdlib_fora_do_bundle_196  # noqa: E402
        """), encoding="utf-8")
        sys.path.insert(0, self._tmp.name)
        _limpa_modulos(PKG)

    def tearDown(self):
        _limpa_modulos(PKG)
        sys.path.remove(self._tmp.name)
        self._tmp.cleanup()

    def test_segunda_tentativa_sem_purge_da_o_erro_falso(self):
        with self.assertRaises(ModuleNotFoundError):  # 1ª: a causa real
            importlib.import_module(PKG)
        # o Python tirou o topo de sys.modules, mas o submódulo já carregado ficou
        self.assertNotIn(PKG, sys.modules)
        self.assertIn(f"{PKG}.autograd", sys.modules)
        with self.assertRaises(AttributeError) as cm:  # 2ª: o falso circular import da #196
            importlib.import_module(PKG)
        self.assertIn("partially initialized module", str(cm.exception))
        self.assertIn("has no attribute 'autograd'", str(cm.exception))

    def test_purge_devolve_a_causa_real_na_segunda_tentativa(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module(PKG)
        removidos = diarize.purge_stale_submodules(PKG)
        self.assertEqual(removidos, [f"{PKG}.autograd"])
        with self.assertRaises(ModuleNotFoundError) as cm:  # de novo a causa REAL
            importlib.import_module(PKG)
        self.assertEqual(cm.exception.name, "modulo_de_stdlib_fora_do_bundle_196")

    def test_purge_nao_mexe_em_pacote_vivo(self):
        sys.modules[PKG] = types.ModuleType(PKG)
        sys.modules[f"{PKG}.autograd"] = types.ModuleType(f"{PKG}.autograd")
        self.assertEqual(diarize.purge_stale_submodules(PKG), [])
        self.assertIn(f"{PKG}.autograd", sys.modules)

    def test_purge_sem_nada_e_noop(self):
        self.assertEqual(diarize.purge_stale_submodules("pacote_que_nunca_importou_196"), [])


class MensagemDeErroTests(unittest.TestCase):
    def test_torch_ausente_e_falta_do_extra(self):
        e = ModuleNotFoundError("No module named 'torch'", name="torch")
        self.assertTrue(diarize.deps_error_message(e).startswith("dependências ausentes"))

    def test_pyannote_audio_ausente_e_falta_do_extra(self):
        e = ModuleNotFoundError("No module named 'pyannote.audio'", name="pyannote.audio")
        self.assertTrue(diarize.deps_error_message(e).startswith("dependências ausentes"))

    def test_modulo_interno_ausente_nao_e_falta_do_extra(self):
        """O caso da #197: pyannote instalado, unittest.mock fora do bundle."""
        e = ModuleNotFoundError("No module named 'unittest.mock'", name="unittest.mock")
        msg = diarize.deps_error_message(e)
        self.assertNotIn("falta o extra", msg.split("—")[0])
        self.assertIn("import interno falhou", msg)
        self.assertIn("unittest.mock", msg)
        self.assertIn("Reinstale os componentes", msg)

    def test_scipy_parcial_nao_e_falta_do_extra(self):
        e = ModuleNotFoundError("No module named 'scipy.cluster'", name="scipy.cluster")
        self.assertIn("import interno falhou", diarize.deps_error_message(e))

    def test_falso_circular_import_nao_e_falta_do_extra(self):
        e = AttributeError("partially initialized module 'torch' has no attribute 'autograd'")
        msg = diarize.deps_error_message(e)
        self.assertIn("import interno falhou", msg)
        self.assertIn("AttributeError", msg)

    def test_dll_que_nao_carrega_nao_e_falta_do_extra(self):
        e = OSError("[WinError 126] Error loading c10_cuda.dll or one of its dependencies")
        self.assertIn("import interno falhou", diarize.deps_error_message(e))

    def test_so_a_primeira_linha_do_erro(self):
        e = RuntimeError("linha 1\nlinha 2 enorme")
        msg = diarize.deps_error_message(e)
        self.assertIn("linha 1", msg)
        self.assertNotIn("linha 2", msg)


class _FinderQueQuebra(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Faz `import <nome>` levantar `erro` na execução do módulo (import interno quebrado)."""

    def __init__(self, nome: str, erro: BaseException):
        self.nome, self.erro, self.pedidos = nome, erro, 0

    def find_spec(self, fullname, path=None, target=None):
        if fullname == self.nome:
            self.pedidos += 1
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        raise self.erro


class DiarizeComImportQuebradoTests(unittest.TestCase):
    """diarize() com torch importável mas o pyannote morrendo num import interno
    (#197): a razão vai no meta com a mensagem certa, e a nota segue sem vozes."""

    def setUp(self):
        self._salvos = {m: sys.modules.pop(m) for m in list(sys.modules)
                        if m == "torch" or m.startswith("torch.")
                        or m == "pyannote.audio" or m.startswith("pyannote.audio.")}
        sys.modules["torch"] = types.ModuleType("torch")  # torch "instalado"
        # o pai `pyannote` precisa existir (no CI não há pyannote nenhum): sem ele
        # o import morreria no pai, e o caso testado é o filho quebrando por dentro
        self._pai_falso = "pyannote" not in sys.modules
        if self._pai_falso:
            pai = types.ModuleType("pyannote")
            pai.__path__ = []
            sys.modules["pyannote"] = pai
        self._finder = _FinderQueQuebra(
            "pyannote.audio", ModuleNotFoundError("No module named 'scipy.cluster'", name="scipy.cluster"))
        sys.meta_path.insert(0, self._finder)

    def tearDown(self):
        sys.meta_path.remove(self._finder)
        _limpa_modulos("torch")
        sys.modules.pop("pyannote.audio", None)
        if self._pai_falso:
            sys.modules.pop("pyannote", None)
        sys.modules.update(self._salvos)

    def test_meta_recebe_a_causa_real(self):
        meta: dict = {}
        cfg = Diarization(enabled=True, hf_token="hf_x")
        out = diarize.diarize(Path("nao_existe.wav"), cfg, meta=meta)
        self.assertIsNone(out)
        self.assertGreaterEqual(self._finder.pedidos, 1)
        self.assertIn("import interno falhou", meta["diarization_error"])
        self.assertIn("scipy.cluster", meta["diarization_error"])
        self.assertNotIn("falta o extra", meta["diarization_error"].split("—")[0])


class CheckVoicesTests(unittest.TestCase):
    """`scribadev components --check` / doctor / smoke do CI: importa o que a
    separação de vozes importa, sem baixar nada, e classifica a falha."""

    def setUp(self):
        self._salvos = {m: sys.modules.pop(m) for m in list(sys.modules)
                        if m == "torch" or m.startswith("torch.")
                        or m == "pyannote.audio" or m.startswith("pyannote.audio.")}
        self._finders: list = []
        self._criados: list[str] = []
        self._substituidos: dict = {}  # módulo real que um _fake() cobriu (volta no tearDown)

    def tearDown(self):
        for f in self._finders:
            sys.meta_path.remove(f)
        _limpa_modulos("torch")
        _limpa_modulos("pyannote.audio")
        for nome in self._criados:
            sys.modules.pop(nome, None)
        sys.modules.update(self._substituidos)
        sys.modules.update(self._salvos)

    def _quebra(self, nome, erro):
        f = _FinderQueQuebra(nome, erro)
        sys.meta_path.insert(0, f)
        self._finders.append(f)
        return f

    def _fake(self, nome, versao="9.9"):
        # os pais também precisam existir (o purge de órfãos apagaria um
        # `pyannote.audio` falso cujo `pyannote` não está carregado)
        partes = nome.split(".")
        for i in range(1, len(partes)):
            pai = ".".join(partes[:i])
            if pai not in sys.modules:
                p = types.ModuleType(pai)
                p.__path__ = []
                sys.modules[pai] = p
                self._criados.append(pai)
        m = types.ModuleType(nome)
        m.__version__ = versao
        m.__path__ = []  # pacote: os submódulos podem ser resolvidos por outro finder
        if nome in sys.modules and nome not in self._substituidos:
            self._substituidos[nome] = sys.modules[nome]
        elif nome not in sys.modules:
            self._criados.append(nome)
        sys.modules[nome] = m
        return m

    def test_torch_ausente_e_falta_do_extra(self):
        from scriba import components

        self._quebra("torch", ModuleNotFoundError("No module named 'torch'", name="torch"))
        ok, detalhe = components.check_voices()
        self.assertFalse(ok)
        self.assertTrue(detalhe.startswith("torch: dependências ausentes"))

    def test_import_interno_quebrado_no_pyannote(self):
        from scriba import components

        self._fake("torch")
        self._fake("pyannote")  # o pai existe (no CI não há pyannote nenhum)
        self._quebra("pyannote.audio",
                     ModuleNotFoundError("No module named 'unittest.mock'", name="unittest.mock"))
        ok, detalhe = components.check_voices()
        self.assertFalse(ok)
        self.assertTrue(detalhe.startswith("pyannote.audio: torch/pyannote instalados"))
        self.assertIn("unittest.mock", detalhe)

    def test_submodulo_do_pyannote_quebrado(self):
        """O caso do scipy.cluster (#197): pyannote.audio importa, clustering não."""
        from scriba import components

        self._fake("torch")
        self._fake("pyannote.audio")
        self._fake("pyannote.audio.pipelines")
        self._quebra("pyannote.audio.pipelines.clustering",
                     ModuleNotFoundError("No module named 'scipy.cluster'", name="scipy.cluster"))
        ok, detalhe = components.check_voices()
        self.assertFalse(ok)
        self.assertIn("pyannote.audio.pipelines.clustering:", detalhe)
        self.assertIn("scipy.cluster", detalhe)

    def test_tudo_importa_devolve_versoes(self):
        from scriba import components

        self._fake("torch", "2.11.0")
        self._fake("pyannote.audio", "4.0.4")
        for sub in components.VOICES_IMPORTS[2:]:
            self._fake(sub)
        ok, detalhe = components.check_voices()
        self.assertTrue(ok, detalhe)
        self.assertEqual(detalhe, "torch 2.11.0, pyannote.audio 4.0.4")

    def test_cli_components_check_falha_com_rc_1(self):
        from scriba import cli

        self._quebra("torch", ModuleNotFoundError("No module named 'torch'", name="torch"))
        self.assertEqual(cli.main(["components", "--check"]), 1)

    def test_cli_components_check_ok_com_rc_0(self):
        from scriba import cli, components

        self._fake("torch", "2.11.0")
        self._fake("pyannote.audio", "4.0.4")
        for sub in components.VOICES_IMPORTS[2:]:
            self._fake(sub)
        self.assertEqual(cli.main(["components", "--check"]), 0)


class CloseDoTranscriberTests(unittest.TestCase):
    """close() não pode importar o torch (#196): só esvazia o cache se alguém já o
    carregou. Importar dentro de `except: pass` era o que envenenava o sys.modules
    para a diarização logo em seguida."""

    def setUp(self):
        self._salvos = {m: sys.modules.pop(m) for m in list(sys.modules)
                        if m == "torch" or m.startswith("torch.")}
        self._finder = _FinderQueQuebra("torch", ModuleNotFoundError("No module named 'unittest.mock'",
                                                                    name="unittest.mock"))
        sys.meta_path.insert(0, self._finder)

    def tearDown(self):
        sys.meta_path.remove(self._finder)
        _limpa_modulos("torch")
        sys.modules.update(self._salvos)

    def test_close_nao_importa_torch(self):
        tr = Transcriber(Whisper())
        tr.close()
        self.assertEqual(self._finder.pedidos, 0, "close() tentou importar o torch")
        self.assertNotIn("torch", sys.modules)

    def test_close_esvazia_o_cache_se_o_torch_ja_esta_carregado(self):
        chamadas = []
        fake = types.ModuleType("torch")
        fake.cuda = types.SimpleNamespace(is_available=lambda: True,
                                          empty_cache=lambda: chamadas.append("empty"))
        sys.modules["torch"] = fake
        Transcriber(Whisper()).close()
        self.assertEqual(chamadas, ["empty"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
