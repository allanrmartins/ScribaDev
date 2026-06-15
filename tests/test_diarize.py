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


if __name__ == "__main__":
    unittest.main(verbosity=2)
