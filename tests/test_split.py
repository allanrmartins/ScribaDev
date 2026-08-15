"""Testes da lógica pura do corte retroativo (scriba.split, #37).

O orquestrador split_recording (ffmpeg + arquivos + re-resumo) é validado por
integração; aqui cobrimos as três funções puras: parse_offset, slice_transcript
e split_meta.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.split import parse_offset, slice_transcript, split_meta  # noqa: E402


class ParseOffsetTests(unittest.TestCase):
    def test_formatos_validos(self):
        self.assertEqual(parse_offset("90"), 90.0)
        self.assertEqual(parse_offset("11:20"), 680.0)
        self.assertEqual(parse_offset("0:05"), 5.0)
        self.assertEqual(parse_offset("1:02:03"), 3723.0)
        self.assertEqual(parse_offset("  11:03  "), 663.0)  # aparado
        self.assertAlmostEqual(parse_offset("2:30.5"), 150.5)

    def test_invalidos(self):
        for bad in ("abc", "1:2:3:4", "", ":", "1:xx"):
            with self.assertRaises(ValueError):
                parse_offset(bad)

    def test_negativo(self):
        with self.assertRaises(ValueError):
            parse_offset("-5")


class SliceTranscriptTests(unittest.TestCase):
    def _turns(self):
        return [
            {"start": 0.0, "end": 10.0, "speaker": "Eu", "text": "a"},
            {"start": 100.0, "end": 110.0, "speaker": "Guilherme", "text": "b"},
            {"start": 250.0, "end": 260.0, "speaker": "Eu", "text": "c"},
        ]

    def test_divide_por_offset(self):
        p1, p2 = slice_transcript(self._turns(), 200.0)
        self.assertEqual([t["text"] for t in p1], ["a", "b"])
        self.assertEqual([t["text"] for t in p2], ["c"])

    def test_parte2_rebaseia_o_tempo(self):
        _p1, p2 = slice_transcript(self._turns(), 200.0)
        self.assertEqual(p2[0]["start"], 50.0)   # 250 - 200
        self.assertEqual(p2[0]["end"], 60.0)     # 260 - 200

    def test_turno_na_fronteira_vai_para_onde_comeca(self):
        # um turno que atravessa o corte (start < offset <= end) fica INTEIRO na parte 1
        turns = [{"start": 190.0, "end": 210.0, "speaker": "Eu", "text": "x"}]
        p1, p2 = slice_transcript(turns, 200.0)
        self.assertEqual(len(p1), 1)
        self.assertEqual(p2, [])

    def test_start_exatamente_no_offset_vai_para_parte2(self):
        turns = [{"start": 200.0, "end": 205.0, "speaker": "Eu", "text": "x"}]
        p1, p2 = slice_transcript(turns, 200.0)
        self.assertEqual(p1, [])
        self.assertEqual(p2[0]["start"], 0.0)  # rebaseado e clampado

    def test_nao_perde_nem_duplica_turnos(self):
        p1, p2 = slice_transcript(self._turns(), 105.0)
        self.assertEqual(len(p1) + len(p2), 3)

    def test_nao_muta_a_entrada(self):
        turns = self._turns()
        slice_transcript(turns, 200.0)
        self.assertEqual(turns[2]["start"], 250.0)  # original intacto


class SplitMetaTests(unittest.TestCase):
    def _meta(self):
        return {
            "started_at": "2026-07-02T09:30:19",
            "ended_at": "2026-07-02T09:41:22",
            "duration_seconds": 663.0,
            "status": "done",
            "streams": {
                "mic": {"file": "mic.opus", "offset_seconds": 0.0, "audio_seconds": 663.0},
                "loopback": {"file": "loopback.opus", "offset_seconds": 0.0, "audio_seconds": 663.0},
            },
            "meeting_title": "Daily reforma tributária Vetra",
            "title": "Daily reforma tributária Vetra",
            "client": "Vetra",
            "export_path": "C:\\x\\2026-07-02_09-30_reuniao.md",
            "speakers_recognized": ["Ricardo Nunes"],
        }

    def test_parte1(self):
        m1, _m2 = split_meta(self._meta(), 300.0, {"mic": 300.0, "loopback": 300.0}, {"mic": 363.0, "loopback": 363.0})
        self.assertEqual(m1["started_at"], "2026-07-02T09:30:19")  # início preservado
        self.assertEqual(m1["ended_at"], "2026-07-02T09:35:19")    # início + 300 s
        self.assertEqual(m1["duration_seconds"], 300.0)
        self.assertEqual(m1["status"], "transcribed")
        self.assertEqual(m1["streams"]["mic"]["audio_seconds"], 300.0)
        self.assertEqual(m1["meeting_title"], "Daily reforma tributária Vetra")  # mantido
        for k in ("title", "client", "export_path", "speakers_recognized"):
            self.assertNotIn(k, m1)  # limpos p/ o re-resumo regenerar

    def test_parte2(self):
        _m1, m2 = split_meta(self._meta(), 300.0, {"mic": 300.0, "loopback": 300.0}, {"mic": 363.0, "loopback": 363.0})
        self.assertEqual(m2["started_at"], "2026-07-02T09:35:19")  # início + 300 s
        self.assertEqual(m2["ended_at"], "2026-07-02T09:41:22")    # fim original preservado
        self.assertEqual(m2["duration_seconds"], 363.0)            # 663 - 300
        self.assertEqual(m2["status"], "transcribed")
        self.assertEqual(m2["meeting_title"], "")                  # zerado -> IA titula
        self.assertEqual(m2["streams"]["mic"]["audio_seconds"], 363.0)
        self.assertEqual(m2["streams"]["mic"]["offset_seconds"], 0.0)
        for k in ("title", "client", "export_path", "speakers_recognized"):
            self.assertNotIn(k, m2)

    def test_nao_muta_o_meta_original(self):
        meta = self._meta()
        split_meta(meta, 300.0, {"mic": 300.0}, {"mic": 363.0})
        self.assertEqual(meta["status"], "done")           # original intacto
        self.assertEqual(meta["duration_seconds"], 663.0)
        self.assertIn("title", meta)


if __name__ == "__main__":
    unittest.main()
