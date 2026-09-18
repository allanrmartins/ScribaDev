"""Recorte de fala pelo VAD antes do MLX (#202): funções puras de recorte e de
remapeamento de tempos, mais o Silero real num áudio sem fala.

O caso que motivou: mic de 19,5 min com 2,7 min de fala; sem o recorte o
mlx-whisper inventava "A CIDADE NO BRASIL" 17 vezes no silêncio. O contrato aqui
é que o áudio entregue ao modelo só tem fala e que os tempos dos segmentos voltam
ao relógio do stream original (merge e diarização dependem deles).
"""

import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import vadcut  # noqa: E402
from scriba.transcriber import Segment  # noqa: E402

_HAS_NUMPY = importlib.util.find_spec("numpy") is not None
_HAS_FW = importlib.util.find_spec("faster_whisper") is not None

SR = 16000


def _chunks(*pairs_s):
    """[(início_s, fim_s), ...] -> trechos em amostras."""
    return [{"start": int(a * SR), "end": int(b * SR)} for a, b in pairs_s]


class TimeMapTests(unittest.TestCase):
    # stream de 60 s com fala em 10-15 s, 30-40 s e 55-58 s -> comprimido tem 18 s
    CH = _chunks((10, 15), (30, 40), (55, 58))

    def test_dentro_do_primeiro_trecho_desloca_pelo_inicio(self):
        tm = vadcut.TimeMap(self.CH, SR)
        self.assertEqual(tm.to_original(0.0), 10.0)
        self.assertEqual(tm.to_original(2.5), 12.5)

    def test_segundo_trecho_pula_o_silencio_entre_eles(self):
        tm = vadcut.TimeMap(self.CH, SR)
        # 5 s comprimidos = fim do 1º trecho = início do 2º (30 s no original)
        self.assertEqual(tm.to_original(5.0), 30.0)
        self.assertEqual(tm.to_original(7.0), 32.0)
        self.assertEqual(tm.to_original(16.0), 56.0)

    def test_fim_exatamente_na_emenda_fica_no_trecho_anterior(self):
        tm = vadcut.TimeMap(self.CH, SR)
        # um segmento que termina em 5,0 s comprimidos acabou no fim do 1º trecho
        # (15 s), não no início do 2º (30 s) — senão o fim vinha depois do início
        # do segmento seguinte e o merge via um turno de 15 s de silêncio
        self.assertEqual(tm.to_original(5.0, is_end=True), 15.0)
        self.assertEqual(tm.to_original(5.0, is_end=False), 30.0)

    def test_passou_do_fim_gruda_no_fim_do_ultimo_trecho(self):
        tm = vadcut.TimeMap(self.CH, SR)
        # o Whisper arredonda o fim p/ cima (janelas de 20 ms / padding)
        self.assertEqual(tm.to_original(18.3, is_end=True), 58.0)
        self.assertEqual(tm.to_original(25.0), 58.0)

    def test_sem_trechos_e_identidade(self):
        tm = vadcut.TimeMap([], SR)
        self.assertEqual(tm.to_original(3.3), 3.3)


class RestoreSegmentsTests(unittest.TestCase):
    CH = _chunks((10, 15), (30, 40))

    def test_segmentos_voltam_ao_relogio_original(self):
        segs = [Segment(0.0, 4.0, "olá"), Segment(5.0, 8.0, "tudo bem")]
        out = vadcut.restore_segments(segs, self.CH, SR)
        self.assertEqual([(s.start, s.end, s.text) for s in out],
                         [(10.0, 14.0, "olá"), (30.0, 33.0, "tudo bem")])

    def test_segmento_que_cruza_a_emenda_nao_fica_invertido(self):
        # começa no 1º trecho e termina no 2º: fim > início sempre
        out = vadcut.restore_segments([Segment(4.0, 6.0, "x")], self.CH, SR)
        self.assertEqual((out[0].start, out[0].end), (14.0, 31.0))
        self.assertGreaterEqual(out[0].end, out[0].start)

    def test_fim_nunca_antes_do_inicio(self):
        out = vadcut.restore_segments([Segment(5.0, 5.0, "x")], self.CH, SR)
        self.assertGreaterEqual(out[0].end, out[0].start)


@unittest.skipUnless(_HAS_NUMPY, "numpy não instalado")
class ConcatTests(unittest.TestCase):
    def test_concatena_so_os_trechos(self):
        import numpy as np

        audio = np.arange(100, dtype=np.float32)
        out = vadcut.concat_speech(audio, [{"start": 10, "end": 12}, {"start": 50, "end": 53}])
        self.assertEqual(out.tolist(), [10.0, 11.0, 50.0, 51.0, 52.0])

    def test_sem_trechos_vira_vazio(self):
        import numpy as np

        self.assertEqual(len(vadcut.concat_speech(np.zeros(10, dtype=np.float32), [])), 0)

    def test_describe_resume_em_minutos(self):
        txt = vadcut.describe(_chunks((0, 60), (120, 180)), 20 * 60 * SR)
        self.assertIn("2.0 de 20.0 min", txt)
        self.assertIn("2 trecho(s)", txt)


@unittest.skipUnless(_HAS_NUMPY and _HAS_FW, "faster_whisper/numpy não instalados")
class SileroTests(unittest.TestCase):
    def test_silencio_nao_tem_fala(self):
        """O caso que dispara a alucinação: 30 s mudos. O Silero real não acha fala
        nenhuma — e o provider então nem chama o modelo."""
        import numpy as np

        self.assertEqual(vadcut.detect_speech(np.zeros(30 * SR, dtype=np.float32)), [])

    def test_params_do_usuario_sao_aceitos(self):
        import numpy as np

        # a calibragem do faster-whisper (threshold/min_silence) vale aqui também
        out = vadcut.detect_speech(np.zeros(5 * SR, dtype=np.float32),
                                   params={"threshold": 0.3, "min_silence_duration_ms": 500})
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
