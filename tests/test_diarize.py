"""Testes de scriba.diarize._speaker_kwargs (#2): precedência de num_speakers.

O topo de scriba.diarize é leve (torch/pyannote só são importados DENTRO de
diarize()), então estes testes rodam mesmo no checkout sem o extra [diarization].
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import diarize  # noqa: E402
from scriba.config import Diarization  # noqa: E402
from scriba.transcriber import Segment  # noqa: E402


class _FakeAnn:
    def __init__(self, labels):
        self._labels = labels

    def labels(self):
        return self._labels


class _FakeResult:
    def __init__(self, emb):
        self.speaker_embeddings = emb


class SpeakerKwargsTests(unittest.TestCase):
    def test_num_speakers_trava_e_ignora_max(self):
        # num_speakers tem precedência: NUNCA combinar com max_speakers
        cfg = Diarization(max_speakers=5)
        self.assertEqual(diarize._speaker_kwargs(cfg, 3), {"num_speakers": 3})

    def test_sem_num_usa_max_speakers_do_config(self):
        self.assertEqual(diarize._speaker_kwargs(Diarization(max_speakers=4), None), {"max_speakers": 4})

    def test_sem_nada_e_automatico(self):
        self.assertEqual(diarize._speaker_kwargs(Diarization(), None), {})

    def test_max_speakers_0_ou_1_e_automatico(self):
        self.assertEqual(diarize._speaker_kwargs(Diarization(max_speakers=0), None), {})
        self.assertEqual(diarize._speaker_kwargs(Diarization(max_speakers=1), None), {})

    def test_num_speakers_invalido_cai_no_config(self):
        # 0/negativo (ou vazio vindo da janela) é ignorado; volta ao comportamento do config
        self.assertEqual(diarize._speaker_kwargs(Diarization(max_speakers=4), 0), {"max_speakers": 4})
        self.assertEqual(diarize._speaker_kwargs(Diarization(), 0), {})

    def test_num_speakers_um_e_valido(self):
        # 1 voz remota é legítimo (call 1-a-1): trava em 1 cluster, sem diarização espúria
        self.assertEqual(diarize._speaker_kwargs(Diarization(), 1), {"num_speakers": 1})


class ExtractEmbeddingsTests(unittest.TestCase):
    def test_casa_label_a_vetor_na_ordem(self):
        import numpy as np

        ann = _FakeAnn(["SPEAKER_00", "SPEAKER_01"])
        res = _FakeResult(np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32))
        out = diarize._extract_embeddings(res, ann)
        self.assertEqual(list(out), ["SPEAKER_00", "SPEAKER_01"])
        self.assertEqual(out["SPEAKER_00"], [1.0, 2.0])
        self.assertEqual(out["SPEAKER_01"], [3.0, 4.0])

    def test_sem_embeddings_vira_dict_vazio(self):
        self.assertEqual(diarize._extract_embeddings(_FakeResult(None), _FakeAnn(["SPEAKER_00"])), {})

    def test_descarta_vetor_com_nan(self):
        import numpy as np

        ann = _FakeAnn(["SPEAKER_00", "SPEAKER_01"])
        res = _FakeResult(np.array([[1.0, 2.0], [np.nan, 4.0]], dtype=np.float32))
        out = diarize._extract_embeddings(res, ann)
        self.assertIn("SPEAKER_00", out)
        self.assertNotIn("SPEAKER_01", out)  # NaN não entra no store


class AssignSpeakersTests(unittest.TestCase):
    def test_numera_por_ordem_de_aparicao_e_devolve_order(self):
        segs = [Segment(0.0, 1.0, "ola"), Segment(1.0, 2.0, "oi")]
        turns = [(0.0, 1.0, "SPEAKER_01"), (1.0, 2.0, "SPEAKER_00")]
        grouped, order = diarize.assign_speakers(segs, turns)
        # 1ª fala sobrepõe SPEAKER_01 → vira Participante 1 (ordem de aparição, não alfabética)
        self.assertEqual(order["SPEAKER_01"], 1)
        self.assertEqual(order["SPEAKER_00"], 2)
        self.assertIn("Participante 1", grouped)
        self.assertIn("Participante 2", grouped)

    def test_segmento_sem_sobreposicao_cai_em_participantes(self):
        segs = [Segment(10.0, 11.0, "sozinho")]
        grouped, order = diarize.assign_speakers(segs, [(0.0, 1.0, "SPEAKER_00")])
        self.assertIn("Participantes", grouped)
        self.assertEqual(order, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
