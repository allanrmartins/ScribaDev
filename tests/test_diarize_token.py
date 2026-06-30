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


if __name__ == "__main__":
    unittest.main(verbosity=2)
