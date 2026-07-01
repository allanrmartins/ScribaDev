"""Testes de scriba.diarize.test_token / _classify_hf_error: validação do token HF
sem rodar o pipeline real (não importa numpy/pyannote, então roda em qualquer ambiente)."""

import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import diarize  # noqa: E402


class ClassifyHfErrorTests(unittest.TestCase):
    def test_token_invalido(self):
        msg = diarize._classify_hf_error(Exception("401 Client Error: Unauthorized"), "m")
        self.assertIn("Token inválido", msg)

    def test_termos_nao_aceitos(self):
        msg = diarize._classify_hf_error(Exception("403 Forbidden: gated repo, accept the terms"), "pyannote/x")
        self.assertIn("termos", msg)
        self.assertIn("pyannote/x", msg)  # cita o modelo na dica

    def test_sem_rede(self):
        msg = diarize._classify_hf_error(Exception("Max retries exceeded: getaddrinfo failed"), "m")
        self.assertIn("conexão", msg)

    def test_desconhecido_devolve_crua(self):
        msg = diarize._classify_hf_error(Exception("algo muito estranho"), "m")
        self.assertIn("algo muito estranho", msg)

    def test_classifica_so_a_primeira_linha(self):
        msg = diarize._classify_hf_error(Exception("boom\nlinha2\nlinha3"), "m")
        self.assertNotIn("linha2", msg)  # não vaza o traceback inteiro


class TestTokenTests(unittest.TestCase):
    def test_token_vazio(self):
        ok, msg = diarize.test_token("pyannote/x", "")
        self.assertFalse(ok)
        self.assertIn("Informe o token", msg)

    def test_token_so_espacos(self):
        ok, _ = diarize.test_token("pyannote/x", "   ")
        self.assertFalse(ok)

    def test_pyannote_ausente_falha_graciosa(self):
        try:
            present = importlib.util.find_spec("pyannote.audio") is not None
        except ModuleNotFoundError:
            present = False  # pacote-pai 'pyannote' ausente: find_spec levanta, não devolve None
        if present:
            self.skipTest("pyannote instalado — o caso de ausência não se aplica")
        ok, msg = diarize.test_token("pyannote/x", "hf_faketoken")
        self.assertFalse(ok)
        self.assertIn("não instalada", msg)


class DiarizeMetaErrorTests(unittest.TestCase):
    """diarize.diarize grava a razão da falha em meta['diarization_error'] p/ o app
    principal logar no scriba.log central (#22 — pedido do Allan)."""

    @staticmethod
    def _cfg(**kw):
        import dataclasses

        from scriba.config import Diarization

        return dataclasses.replace(Diarization(), **kw)

    def test_desabilitada_nao_marca(self):
        meta: dict = {}
        self.assertIsNone(diarize.diarize(Path("x.wav"), self._cfg(enabled=False), meta=meta))
        self.assertNotIn("diarization_error", meta)  # desligada de propósito não é erro

    def test_sem_token_marca_erro(self):
        meta: dict = {}
        self.assertIsNone(diarize.diarize(Path("x.wav"), self._cfg(enabled=True, hf_token=""), meta=meta))
        self.assertIn("token", meta.get("diarization_error", "").lower())

    def test_sem_meta_nao_quebra(self):
        # meta=None (default) é válido: só loga, não tenta gravar
        self.assertIsNone(diarize.diarize(Path("x.wav"), self._cfg(enabled=True, hf_token="")))


class ExtractAnnotationTests(unittest.TestCase):
    """diarize._extract_annotation / _unwrap_result: tolerância aos formatos de retorno do
    pyannote, incl. a LISTA do batch inference (4.0.5+) que dava "0 voz(es) em 0 trechos" (#24)."""

    class _Ann:  # simula pyannote.core.Annotation (só precisa de itertracks)
        def itertracks(self, yield_label=True):
            return iter(())

    class _DiarizeOutput:  # simula pyannote 4.x: objeto com .speaker_diarization
        def __init__(self, ann):
            self.speaker_diarization = ann
            self.speaker_embeddings = None

    def test_annotation_direta(self):  # 3.x / 4.0.4
        ann = self._Ann()
        self.assertIs(diarize._extract_annotation(ann), ann)

    def test_diarize_output_4x(self):
        ann = self._Ann()
        self.assertIs(diarize._extract_annotation(self._DiarizeOutput(ann)), ann)

    def test_lista_batch_inference(self):
        # 4.0.5+: apply() devolve [DiarizeOutput] — o que quebrava a diarização
        ann = self._Ann()
        out = self._DiarizeOutput(ann)
        self.assertIs(diarize._unwrap_result([out]), out)        # desembrulha o item único
        self.assertIs(diarize._extract_annotation([out]), ann)   # e ainda acha a Annotation
        self.assertIs(diarize._extract_annotation(diarize._unwrap_result([out])), ann)

    def test_generator_batch_inference(self):
        # 4.0.5+ real (log do reporter: tipo=generator): apply() devolve um GERADOR lazy
        ann = self._Ann()
        out = self._DiarizeOutput(ann)
        self.assertIs(diarize._unwrap_result(x for x in (out,)), out)  # consome + desembrulha
        self.assertIs(diarize._extract_annotation(diarize._unwrap_result(x for x in (out,))), ann)

    def test_lista_de_annotation(self):
        ann = self._Ann()
        self.assertIs(diarize._extract_annotation([ann]), ann)

    def test_formato_irreconhecido_vira_none(self):
        self.assertIsNone(diarize._extract_annotation("isto nao e diarizacao"))
        self.assertIsNone(diarize._extract_annotation(None))

    def test_unwrap_so_lista_de_um(self):
        ann = self._Ann()
        self.assertIs(diarize._unwrap_result(ann), ann)            # não-lista: passa direto
        self.assertEqual(diarize._unwrap_result([1, 2]), [1, 2])   # >1 item: não desembrulha

    def test_run_pipe_usa_apply_nao_call(self):
        # cerne do fix: pyannote 4.0.5+ faz pipe() (via __call__) devolver um GERADOR de lote;
        # pipe.apply() vai direto ao DiarizeOutput. _run_pipe deve preferir apply.
        ann = self._Ann()
        out = self._DiarizeOutput(ann)
        calls = []

        class FakePipe:
            def apply(self, audio, **kw):
                calls.append("apply")
                return out
            def __call__(self, audio, **kw):
                calls.append("call")
                return (x for x in (out,))  # o __call__ com lote devolveria um gerador

        res = diarize._run_pipe(FakePipe(), {"waveform": None, "sample_rate": 16000}, {})
        self.assertEqual(calls, ["apply"])   # foi pelo apply, não pelo __call__
        self.assertIsNotNone(res)            # e reconheceu o retorno (não caiu em "0 vozes")


if __name__ == "__main__":
    unittest.main(verbosity=2)
