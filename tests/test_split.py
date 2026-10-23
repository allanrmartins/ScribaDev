"""Testes da lógica pura do corte retroativo (scriba.split, #37).

O orquestrador split_recording (ffmpeg + arquivos + re-resumo) é validado por
integração; aqui cobrimos as três funções puras: parse_offset, slice_transcript
e split_meta. O corte pelas marcas de título (title_cuts) também é puro; o
split_by_titles roda de ponta a ponta com ffmpeg quando ele está no PATH.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import split  # noqa: E402
from scriba.split import parse_offset, slice_transcript, split_meta, title_cuts  # noqa: E402


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


def _meta_com_marcas(marcas: list[tuple[int, str]], total: float = 3391.0) -> dict:
    """Meta de gravação iniciada às 10:06:51 com marcas de título (offset, título)."""
    from datetime import datetime, timedelta

    t0 = datetime(2026, 10, 23, 10, 6, 51)
    return {
        "started_at": t0.isoformat(timespec="seconds"),
        "duration_seconds": total,
        "title_marks": [{"at": (t0 + timedelta(seconds=s)).isoformat(timespec="seconds"),
                         "title": t} for s, t in marcas],
    }


class TitleCutsTests(unittest.TestCase):
    """Call em espera + outra call: o mic não fecha e só o título muda (2026-10-23)."""

    FLORESTAL = "[Florestal] Testes integrados - Módulo FM"
    # falas reais em volta da troca: a Florestal termina em 2746,3 e o colega
    # atende em 2747,1, alguns segundos antes da 1ª amostra do título novo
    TURNOS = [
        {"start": 2673.6, "end": 2718.6, "speaker": "Participantes", "text": "a"},
        {"start": 2718.6, "end": 2746.3, "speaker": "Participantes", "text": "b"},
        {"start": 2747.1, "end": 2763.1, "speaker": "Participantes", "text": "fala meu"},
        {"start": 2747.3, "end": 2752.8, "speaker": "Eu", "text": "opa"},
    ]

    def test_espera_e_outra_call_corta_na_pausa_antes_do_titulo_novo(self):
        meta = _meta_com_marcas([(20, self.FLORESTAL), (2751, "Ebert Junior")])
        self.assertEqual(title_cuts(meta, self.TURNOS), [(2746.7, "Ebert Junior")])

    def test_um_titulo_so_nao_corta(self):
        self.assertEqual(title_cuts(_meta_com_marcas([(20, self.FLORESTAL)]), self.TURNOS), [])
        self.assertEqual(title_cuts({"started_at": "2026-10-23T10:06:51"}, []), [])

    def test_piscada_de_titulo_nao_corta(self):
        """Espiar a janela de outra reunião por 1 min não é call nova."""
        meta = _meta_com_marcas([(20, self.FLORESTAL), (600, "Outra reunião"), (660, self.FLORESTAL)])
        self.assertEqual(title_cuts(meta, []), [])

    def test_call_curta_no_fim_nao_corta(self):
        meta = _meta_com_marcas([(20, self.FLORESTAL), (3300, "Ebert Junior")])
        self.assertEqual(title_cuts(meta, []), [])

    def test_mesma_call_ad_hoc_com_gente_entrando_nao_corta(self):
        meta = _meta_com_marcas([(20, "Pessoa A"), (900, "Pessoa A, Pessoa B")])
        self.assertEqual(title_cuts(meta, []), [])

    def test_voltar_para_a_call_em_espera_vira_terceira_parte(self):
        meta = _meta_com_marcas([(20, self.FLORESTAL), (1000, "Ebert Junior"), (2000, self.FLORESTAL)])
        cuts = title_cuts(meta, [])
        self.assertEqual([t for _, t in cuts], ["Ebert Junior", self.FLORESTAL])
        # sem fala na janela: corta no meio dela, antes da 1ª amostra do título
        self.assertEqual([o for o, _ in cuts], [992.5, 1992.5])

    def test_titulo_que_estabiliza_tarde_e_da_primeira_reuniao(self):
        meta = _meta_com_marcas([(200, self.FLORESTAL), (2751, "Ebert Junior")])
        self.assertEqual(title_cuts(meta, self.TURNOS), [(2746.7, "Ebert Junior")])


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg ausente")
class SplitByTitlesTests(unittest.TestCase):
    """Corte de ponta a ponta numa pasta recém-transcrita (áudio wav de verdade)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.folder = self.base / "2026" / "10" / "23" / "10-06"
        self.folder.mkdir(parents=True)
        for name in ("mic.wav", "loopback.wav"):
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                 "-t", "400", str(self.folder / name)], check=True)
        meta = _meta_com_marcas([(15, "Reunião A"), (250, "Pessoa B")], total=400.0)
        meta["status"] = "transcribed"
        meta["meeting_title"] = "Reunião A"
        meta["streams"] = {k: {"file": f"{k}.wav", "offset_seconds": 0.0} for k in ("mic", "loopback")}
        (self.folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        turnos = [{"start": 10.0, "end": 238.0, "speaker": "Eu", "text": "a"},
                  {"start": 244.0, "end": 300.0, "speaker": "Eu", "text": "b"}]
        (self.folder / "transcript.json").write_text(json.dumps(turnos), encoding="utf-8")

    def _meta(self, folder: Path) -> dict:
        return json.loads((folder / "meta.json").read_text(encoding="utf-8"))

    def test_corta_em_duas_reunioes_prontas_para_o_resumo(self):
        novas = split.split_by_titles(self.folder)
        self.assertEqual([p.name for p in novas], ["10-10"])  # 10:06:51 + 241 s
        m1, m2 = self._meta(self.folder), self._meta(novas[0])
        self.assertEqual((m1["meeting_title"], m2["meeting_title"]), ("Reunião A", "Pessoa B"))
        self.assertEqual((m1["status"], m2["status"]), ("transcribed", "transcribed"))
        self.assertEqual(m1["duration_seconds"], 241.0)
        t2 = json.loads((novas[0] / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(t2[0]["start"], 3.0)  # a fala das 244 s abre a parte 2
        self.assertFalse(list(self.folder.glob("*.orig")))
        self.assertEqual(split.split_by_titles(self.folder), [])  # idempotente
        self.assertEqual(split.split_by_titles(novas[0]), [])
