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

    def test_min_speakers_sozinho(self):  # #22: mín de vozes na UI
        self.assertEqual(diarize._speaker_kwargs(Diarization(min_speakers=2), None), {"min_speakers": 2})

    def test_min_speakers_0_ou_1_e_automatico(self):
        self.assertEqual(diarize._speaker_kwargs(Diarization(min_speakers=1), None), {})

    def test_min_e_max_juntos(self):
        self.assertEqual(diarize._speaker_kwargs(Diarization(min_speakers=2, max_speakers=5), None),
                         {"max_speakers": 5, "min_speakers": 2})

    def test_min_nao_passa_do_max(self):
        # min=6 com max=3 seria rejeitado pelo pyannote -> min clampa no max
        self.assertEqual(diarize._speaker_kwargs(Diarization(min_speakers=6, max_speakers=3), None),
                         {"max_speakers": 3, "min_speakers": 3})

    def test_num_speakers_ignora_min_e_max(self):
        self.assertEqual(diarize._speaker_kwargs(Diarization(min_speakers=2, max_speakers=5), 3),
                         {"num_speakers": 3})


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


class ReLinkTests(unittest.TestCase):
    """Re-linking de vozes entre blocos (diarização chunked, áudio longo)."""

    def _v(self, *xs):
        import numpy as np
        return np.asarray(xs, dtype=np.float32)

    def test_cosine(self):
        self.assertAlmostEqual(diarize._cosine([1, 0, 0], [1, 0, 0]), 1.0, places=5)
        self.assertAlmostEqual(diarize._cosine([1, 0, 0], [0, 1, 0]), 0.0, places=5)
        self.assertEqual(diarize._cosine([0, 0], [1, 0]), 0.0)  # vetor nulo não quebra

    def test_match_cria_religa_e_separa(self):
        g = []
        self.assertEqual(diarize._match_or_new_global(g, self._v(1, 0, 0)), "G0")
        # voz quase idêntica → mesma global
        self.assertEqual(diarize._match_or_new_global(g, self._v(0.97, 0.03, 0)), "G0")
        self.assertEqual(len(g), 1)
        # voz ortogonal → nova global
        self.assertEqual(diarize._match_or_new_global(g, self._v(0, 1, 0)), "G1")
        self.assertEqual(len(g), 2)

    def test_centroide_acumula(self):
        g = []
        diarize._match_or_new_global(g, self._v(1, 0, 0))
        diarize._match_or_new_global(g, self._v(1, 1, 0))  # cos=0.707 ≥ 0.5 → mesma voz
        self.assertEqual(g[0][1], 2)                        # n de amostras
        self.assertAlmostEqual(float(g[0][0][1]), 0.5, places=5)  # média da 2ª dim


# -- fakes p/ testar _diarize_chunked sem pyannote/torch -----------------------
class _FakeSeg:
    def __init__(self, s, e):
        self.start, self.end = s, e


class _FakeAnnotation:
    def __init__(self, tracks):
        self._tracks = tracks  # [(start, end, label)]

    def labels(self):
        seen = []
        for _s, _e, lab in self._tracks:
            if lab not in seen:
                seen.append(lab)
        return seen

    def itertracks(self, yield_label=True):
        for s, e, lab in self._tracks:
            yield _FakeSeg(s, e), None, lab


class _FakeOut:
    def __init__(self, tracks, emb):
        self.speaker_diarization = _FakeAnnotation(tracks)
        self.speaker_embeddings = emb  # (n_labels, dim), ordem de labels()


class _FakePipe:
    def __init__(self, outs):
        self._outs, self._i = outs, 0

    def __call__(self, audio, **kw):
        o = self._outs[self._i]
        self._i += 1
        return o


class _FakeWave:
    def __init__(self, arr):
        self.arr = arr

    @property
    def shape(self):
        return self.arr.shape

    def __getitem__(self, k):
        return _FakeWave(self.arr[k])

    def clone(self):
        return _FakeWave(self.arr.copy())


class ChunkedDiarizeTests(unittest.TestCase):
    def test_offsets_e_religacao_entre_blocos(self):
        import numpy as np

        sr, chunk_s = 16000, 60
        total = int(2.5 * chunk_s * sr)  # 2,5 blocos
        audio = {"waveform": _FakeWave(np.zeros((1, total), dtype=np.float32)), "sample_rate": sr}
        EA, EB = np.array([1.0, 0, 0], np.float32), np.array([0, 1.0, 0], np.float32)
        # bloco0: voz A · bloco1: voz A (mesma) + voz B · bloco2: voz B
        pipe = _FakePipe([
            _FakeOut([(0.0, 5.0, "SPEAKER_00")], np.stack([EA])),
            _FakeOut([(1.0, 4.0, "SPEAKER_00"), (10.0, 15.0, "SPEAKER_01")], np.stack([EA * 0.97, EB])),
            _FakeOut([(2.0, 8.0, "SPEAKER_00")], np.stack([EB * 0.98])),
        ])
        out = diarize._diarize_chunked(pipe, audio, sr, chunk_s)
        self.assertIsNotNone(out)
        turns, embeddings = out
        # A e B re-ligadas entre blocos → só 2 vozes globais
        self.assertEqual(len(embeddings), 2)
        self.assertEqual(len({lab for *_x, lab in turns}), 2)
        starts = sorted(s for s, _e, _l in turns)
        self.assertIn(0.0, starts)     # bloco0 (offset 0)
        self.assertIn(61.0, starts)    # bloco1 voz A em 1.0 + 60
        self.assertIn(122.0, starts)   # bloco2 voz B em 2.0 + 120
        # a voz A do bloco0 e a do bloco1 têm o MESMO id global
        g_b0 = next(l for s, _e, l in turns if s == 0.0)
        g_b1a = next(l for s, _e, l in turns if s == 61.0)
        self.assertEqual(g_b0, g_b1a)


if __name__ == "__main__":
    unittest.main(verbosity=2)
