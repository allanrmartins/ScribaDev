"""Prints da tela como momentos-chave da call (#229)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from scriba import notes, prints, printwatch, recorder, split
from scriba.printkey_win import VK_SNAPSHOT, is_print_down

_SHOT_A = {"at": "2026-11-09T10:02:00", "offset_seconds": 120.0,
           "file": "prints/print-10h02m00s.png", "source": "pilula"}
_SHOT_B = {"at": "2026-11-09T10:05:00", "offset_seconds": 300.0,
           "file": "prints/print-10h05m00s.png", "source": "tecla"}


class ArquivoTests(unittest.TestCase):
    def test_nome_pelo_horario_e_sem_colisao(self):
        with tempfile.TemporaryDirectory() as d:
            when = datetime(2026, 11, 9, 14, 32, 5)
            p1 = prints.unique_path(Path(d), when)
            self.assertEqual(p1.relative_to(d).as_posix(), "prints/print-14h32m05s.png")
            p1.parent.mkdir()
            p1.write_bytes(b"x")
            self.assertEqual(prints.unique_path(Path(d), when).name, "print-14h32m05s_2.png")

    def test_entrada_do_meta(self):
        folder = Path("/tmp/rec")
        e = prints.entry(folder / "prints" / "print-10h02m00s.png", folder,
                         datetime(2026, 11, 9, 10, 2), 120.04, "pilula")
        self.assertEqual(e, _SHOT_A)
        self.assertEqual(prints.entry(folder / "prints/x.png", folder, datetime(2026, 1, 1), -3, "?")
                         ["offset_seconds"], 0.0)

    def test_shots_of_ordena_e_ignora_entrada_torta(self):
        meta = {"screenshots": [_SHOT_B, {"file": ""}, {"offset_seconds": "x", "file": "a"}, _SHOT_A]}
        self.assertEqual([s["file"] for s in prints.shots_of(meta)], [_SHOT_A["file"], _SHOT_B["file"]])


class TranscricaoTests(unittest.TestCase):
    def test_marca_entra_no_ponto_do_print(self):
        md = "**[00:01:00] Eu:** a\n\n**[00:03:00] Participantes:** b\n\n**[00:06:00] Eu:** c"
        out = prints.interleave(md, [60.0, 180.0, 360.0], [_SHOT_A, _SHOT_B])
        paras = out.split("\n\n")
        self.assertEqual(len(paras), 5)
        self.assertEqual(paras[1], f"**[00:02:00] {prints.MARKER_LABEL}:** print-10h02m00s.png")
        self.assertIn("print-10h05m00s.png", paras[3])
        self.assertTrue(paras[4].endswith("c"))

    def test_print_depois_da_ultima_fala_vai_para_o_fim(self):
        out = prints.interleave("**[00:00:10] Eu:** a", [10.0], [_SHOT_B])
        self.assertTrue(out.endswith("print-10h05m00s.png"))

    def test_formato_inesperado_nao_perde_print(self):
        out = prints.interleave("texto\n\nsolto\n\ndemais", [1.0], [_SHOT_A])
        self.assertIn("print-10h02m00s.png", out)

    def test_marca_sobrevive_ao_fatiamento_do_map_reduce(self):
        from scriba.transcript_search import chunk_transcript

        md = prints.interleave("\n\n".join(f"**[00:00:{i:02d}] Eu:** " + "x" * 50 for i in range(40)),
                               [float(i) for i in range(40)], [{**_SHOT_A, "offset_seconds": 20.5}])
        partes = chunk_transcript(md, max_chars=300)
        self.assertEqual(sum(p.count(prints.MARKER_LABEL) for p in partes), 1)


class ResumoTests(unittest.TestCase):
    RESUMO = ("## Objetivo\nResolver X.\n\n## Contexto\nSD.\n\n## Decisões\n- nada\n")

    def test_bloco_do_prompt_so_com_prints(self):
        self.assertEqual(prints.prompt_block([]), "")
        bloco = prints.prompt_block([_SHOT_A])
        self.assertIn(f"## {prints.SECTION_TITLE}", bloco)
        self.assertIn("[00:02:00] print-10h02m00s.png", bloco)

    def test_ia_esqueceu_a_secao_o_codigo_cria_depois_da_primeira(self):
        out = prints.attach(self.RESUMO, [_SHOT_A])
        secoes = [ln for ln in out.splitlines() if ln.startswith("## ")]
        self.assertEqual(secoes, ["## Objetivo", f"## {prints.SECTION_TITLE}", "## Contexto", "## Decisões"])
        self.assertIn("(a IA não descreveu este print)", out)
        self.assertIn("[![Print 00:02:00](prints/print-10h02m00s.png)](prints/print-10h02m00s.png)", out)

    def test_item_da_ia_ganha_a_imagem_e_o_que_faltou_e_completado(self):
        resumo = self.RESUMO.replace(
            "## Contexto", f"## {prints.SECTION_TITLE}\n- [00:05:00] print-10h05m00s.png - "
                           "tela de erro da VA01\n\n## Contexto")
        out = prints.attach(resumo, [_SHOT_A, _SHOT_B])
        corpo = out.split(f"## {prints.SECTION_TITLE}")[1].split("## Contexto")[0]
        linhas = [ln for ln in corpo.splitlines() if ln.strip()]
        self.assertEqual(len(linhas), 4)  # 2 itens + 2 imagens, em ordem cronológica
        self.assertIn("print-10h02m00s.png", linhas[0])
        self.assertIn("tela de erro da VA01", linhas[2])
        self.assertTrue(linhas[3].lstrip().startswith("[![Print 00:05:00]"))

    def test_descricao_em_varias_linhas_fica_inteira_antes_da_imagem(self):
        resumo = self.RESUMO.replace(
            "## Contexto", f"## {prints.SECTION_TITLE}\n- [00:02:00] print-10h02m00s.png - tela da VA01.\n"
                           "Mostrou o bloqueio Z1 no item 10.\n\n## Contexto")
        out = prints.attach(resumo, [_SHOT_A])
        corpo = out.split(f"## {prints.SECTION_TITLE}")[1].split("## Contexto")[0]
        linhas = [ln for ln in corpo.splitlines() if ln.strip()]
        self.assertEqual(len(linhas), 3)
        self.assertTrue(linhas[0].startswith("- [00:02:00]"))
        self.assertEqual(linhas[1], "  Mostrou o bloqueio Z1 no item 10.")
        self.assertTrue(linhas[2].lstrip().startswith("[![Print"))
        self.assertEqual(prints.attach(out, [_SHOT_A]), out)

    def test_reprocessar_nao_duplica_imagens(self):
        uma = prints.attach(self.RESUMO, [_SHOT_A])
        self.assertEqual(prints.attach(uma, [_SHOT_A]), uma)

    def test_resumo_sem_secoes_recebe_a_secao_no_fim(self):
        out = prints.attach("> Resumo indisponível - a IA não respondeu.", [_SHOT_A])
        self.assertTrue(out.startswith("> Resumo indisponível"))
        self.assertIn(f"## {prints.SECTION_TITLE}", out)

    def test_sem_prints_nada_muda(self):
        self.assertEqual(prints.attach(self.RESUMO, []), self.RESUMO)

    def test_relink_e_pasta_da_copia_exportada(self):
        self.assertEqual(prints.export_dir_name(Path("2026-11-09_14-30_reuniao.md")), "2026-11-09_14-30_prints")
        self.assertEqual(prints.relink("![a](prints/x.png)", "N_prints"), "![a](N_prints/x.png)")

    def test_map_preserva_as_marcas(self):
        self.assertIn(prints.MARKER_LABEL, notes._MAP_SYSTEM)


class CorteTests(unittest.TestCase):
    def test_split_shots_rebaseia_a_parte_2(self):
        p1, p2 = prints.split_shots([_SHOT_A, _SHOT_B], 200.0)
        self.assertEqual([s["file"] for s in p1], [_SHOT_A["file"]])
        self.assertEqual(p2[0]["offset_seconds"], 100.0)

    def test_split_meta_divide_os_prints(self):
        meta = {"started_at": "2026-11-09T10:00:00", "duration_seconds": 400.0, "streams": {},
                "screenshots": [_SHOT_A, _SHOT_B]}
        m1, m2 = split.split_meta(meta, 200.0, {}, {})
        self.assertEqual([s["file"] for s in m1["screenshots"]], [_SHOT_A["file"]])
        self.assertEqual(m2["screenshots"][0]["offset_seconds"], 100.0)
        m1, m2 = split.split_meta(meta, 50.0, {}, {})
        self.assertNotIn("screenshots", m1)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg ausente")
    def test_corte_pelo_titulo_leva_o_arquivo_para_a_parte_2(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "2026" / "11" / "09" / "10-00"
            (folder / "prints").mkdir(parents=True)
            for name in ("mic.wav", "loopback.wav"):
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                                "-t", "400", str(folder / name)], check=True)
            for s in (_SHOT_A, _SHOT_B):
                (folder / s["file"]).write_bytes(b"png")
            meta = {"started_at": "2026-11-09T10:00:00", "duration_seconds": 400.0, "status": "transcribed",
                    "meeting_title": "Reunião A",
                    "title_marks": [{"at": "2026-11-09T10:00:15", "title": "Reunião A"},
                                    {"at": "2026-11-09T10:04:10", "title": "Pessoa B"}],
                    "streams": {k: {"file": f"{k}.wav", "offset_seconds": 0.0} for k in ("mic", "loopback")},
                    "screenshots": [_SHOT_A, _SHOT_B]}
            (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            turnos = [{"start": 10.0, "end": 238.0, "speaker": "Eu", "text": "a"},
                      {"start": 244.0, "end": 380.0, "speaker": "Eu", "text": "b"}]
            (folder / "transcript.json").write_text(json.dumps(turnos), encoding="utf-8")
            (nova,) = split.split_by_titles(folder)
            m1 = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            m2 = json.loads((nova / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual([s["file"] for s in m1["screenshots"]], [_SHOT_A["file"]])
            self.assertTrue((folder / _SHOT_A["file"]).is_file())
            self.assertFalse((folder / _SHOT_B["file"]).exists())
            self.assertTrue((nova / _SHOT_B["file"]).is_file())
            self.assertAlmostEqual(m2["screenshots"][0]["offset_seconds"], 300.0 - m1["duration_seconds"], 1)


class GravadorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rec = recorder.Recording.__new__(recorder.Recording)  # sem abrir áudio
        rec.folder = Path(tmp.name)
        rec.started_at = datetime(2026, 11, 9, 10, 0, 0)
        stream = types.SimpleNamespace(
            wav=types.SimpleNamespace(path=Path("mic.wav"), frames_written=0),
            device_name="dev", rate=16000, channels=1, offset_seconds=0.0)
        rec.mic = rec.loopback = stream
        rec.meeting_title = ""
        rec._title_marks = []
        rec._screenshots = []
        rec._switches = []
        rec._meta_lock = threading.Lock()
        rec._meta_final = False
        rec._write_meta("recording")
        self.rec = rec

    def _meta(self) -> dict:
        return json.loads((self.rec.folder / "meta.json").read_text(encoding="utf-8"))

    def test_registra_no_meta_com_a_posicao_da_gravacao(self):
        p = self.rec.folder / "prints" / "print-10h02m00s.png"
        self.assertTrue(self.rec.note_screenshot(p, datetime(2026, 11, 9, 10, 2), "pilula", 120.0))
        self.assertEqual(self._meta()["screenshots"], [_SHOT_A])

    def test_preserva_o_que_a_pilula_gravou_direto_no_meta(self):
        meta = self._meta()
        meta["num_speakers"] = 4
        (self.rec.folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        self.rec.note_screenshot(self.rec.folder / "prints/a.png", datetime(2026, 11, 9, 10, 1), "tecla", 60)
        self.assertEqual(self._meta()["num_speakers"], 4)

    def test_print_que_chega_depois_do_fim_e_recusado(self):
        self.rec._meta_final = True
        self.assertFalse(self.rec.note_screenshot(self.rec.folder / "prints/a.png",
                                                  datetime(2026, 11, 9, 10, 1), "pilula", 60))
        self.assertNotIn("screenshots", self._meta())


class NotaTests(unittest.TestCase):
    """build_notes de ponta a ponta (IA simulada): marca na transcrição, seção no
    resumo, cópia dos prints junto da nota exportada com os links certos."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="scriba_prints_"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.rec = self.d / "rec" / "10-00"
        (self.rec / "prints").mkdir(parents=True)
        (self.rec / _SHOT_A["file"]).write_bytes(b"png-a")
        self.export = self.d / "notas"
        (self.rec / "meta.json").write_text(json.dumps({
            "status": "transcribed", "started_at": "2026-11-09T10:00:00", "duration_seconds": 400.0,
            "screenshots": [_SHOT_A, _SHOT_B]}), encoding="utf-8")  # B sem arquivo: ignorado
        (self.rec / "transcript.json").write_text(json.dumps(
            [{"start": 60.0, "end": 62.0, "speaker": "Eu", "text": "olha a tela"},
             {"start": 180.0, "end": 182.0, "speaker": "Participantes", "text": "vi"}]), encoding="utf-8")

    def _build(self, resumo="## Objetivo\nX.\n\n## Contexto\nY.\n"):
        seen = {}

        def fake_summary(md, folder):
            seen["md"] = md
            return resumo, "Titulo", ""

        export = self.export
        cfg = types.SimpleNamespace(output=types.SimpleNamespace(resolved_export_dir=lambda: export))
        with mock.patch("scriba.notes.load", return_value=cfg), \
                mock.patch("scriba.notes.generate_summary", side_effect=fake_summary), \
                mock.patch("scriba.notes.load_context_note", return_value=""), \
                mock.patch("scriba.meetings_index.index_meeting"):
            return notes.build_notes(self.rec), seen

    def test_nota_local_e_exportada(self):
        export, seen = self._build()
        self.assertIn(f"**[00:02:00] {prints.MARKER_LABEL}:** print-10h02m00s.png", seen["md"])
        self.assertNotIn("print-10h05m00s", seen["md"])
        local = (self.rec / "notas.md").read_text(encoding="utf-8")
        self.assertIn("](prints/print-10h02m00s.png)", local)
        exp = export.read_text(encoding="utf-8")
        pasta = prints.export_dir_name(export)
        self.assertIn(f"]({pasta}/print-10h02m00s.png)", exp)
        self.assertNotIn("](prints/", exp)
        self.assertEqual((export.parent / pasta / "print-10h02m00s.png").read_bytes(), b"png-a")

    def test_reprocessar_sem_prints_limpa_a_copia(self):
        export, _ = self._build()
        pasta = export.parent / prints.export_dir_name(export)
        meta = json.loads((self.rec / "meta.json").read_text(encoding="utf-8"))
        meta.pop("screenshots")
        (self.rec / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        self._build()
        self.assertFalse(pasta.exists())

    def test_prompt_recebe_o_bloco_dos_prints(self):
        captured = {}

        def fake_complete(system, payload, **kw):
            captured["payload"] = payload
            return "TITULO: T\nCLIENTE: ?\n\n## Objetivo\nX."

        meta = {"screenshots": [_SHOT_A], "duration_seconds": 60}
        (self.rec / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        cfg = types.SimpleNamespace(summary=types.SimpleNamespace(enabled=True, timeout_seconds=60),
                                    whisper=types.SimpleNamespace(hotwords=""))
        with mock.patch("scriba.notes.load", return_value=cfg), \
                mock.patch("scriba.ai.complete", side_effect=fake_complete), \
                mock.patch("scriba.notes._learn_terms"), \
                mock.patch("scriba.notes._vocabulary_block", return_value=""), \
                mock.patch("scriba.notes.load_summary_prompt", return_value="PROMPT"):
            notes.generate_summary("**[00:00:01] Eu:** oi", self.rec)
        self.assertIn("PRINTS DA TELA", captured["payload"])
        self.assertIn("print-10h02m00s.png", captured["payload"])


class PastaDoSistemaTests(unittest.TestCase):
    def test_so_devolve_print_novo_e_completo_uma_vez(self):
        seen = {}
        since = 1000.0
        entries = [("Captura 1.png", 1001.0, 10), (".Captura tmp.png", 1001.0, 5),
                   ("velho.png", 999.0, 10), ("notas.txt", 1001.0, 10)]
        self.assertEqual(prints.new_system_shots(entries, since, seen), [])  # 1ª ronda: mede
        entries[0] = ("Captura 1.png", 1001.0, 20)  # ainda crescendo
        self.assertEqual(prints.new_system_shots(entries, since, seen), [])
        self.assertEqual(prints.new_system_shots(entries, since, seen), ["Captura 1.png"])
        self.assertEqual(prints.new_system_shots(entries, since, seen), [])  # nunca duas vezes

    def test_folder_watch_ignora_o_que_ja_existia_e_copia_o_novo(self):
        import os
        import time

        with tempfile.TemporaryDirectory() as d:
            shots_dir = Path(d) / "Desktop"
            shots_dir.mkdir()
            (shots_dir / "antigo.png").write_bytes(b"old")
            got = []
            w = printwatch.FolderWatch(shots_dir, time.time() - 5, lambda p, when: got.append(p.name))
            for name, _m, _s in printwatch._scan(shots_dir):
                w._seen[name] = -1  # o que start() faz, sem subir a thread
            novo = shots_dir / "Captura de Tela.png"
            novo.write_bytes(b"new")
            os.utime(novo, None)
            w.poll()
            w.poll()
            self.assertEqual(got, ["Captura de Tela.png"])

            rec_dir = Path(d) / "rec"
            rec_dir.mkdir()
            dst = printwatch.copy_into(novo, rec_dir, datetime(2026, 11, 9, 10, 2))
            self.assertEqual(dst.relative_to(rec_dir).as_posix(), "prints/print-10h02m00s.png")
            self.assertEqual(novo.read_bytes(), b"new")  # o original do sistema fica intacto


class TeclaWindowsTests(unittest.TestCase):
    def test_reconhece_print_screen_descendo(self):
        self.assertTrue(is_print_down(0x0100, VK_SNAPSHOT))
        self.assertTrue(is_print_down(0x0104, VK_SNAPSHOT))  # Alt+Print Screen
        self.assertFalse(is_print_down(0x0101, VK_SNAPSHOT))  # soltando
        self.assertFalse(is_print_down(0x0100, 0x41))

    def test_segurar_a_tecla_dispara_uma_vez(self):
        from scriba.printkey_win import _Edge

        e = _Edge()
        disparos = [e.feed(0x0100, VK_SNAPSHOT) for _ in range(5)]  # auto-repetição
        self.assertEqual(disparos, [True, False, False, False, False])
        e.feed(0x0101, VK_SNAPSHOT)  # soltou
        self.assertTrue(e.feed(0x0100, VK_SNAPSHOT))


if __name__ == "__main__":
    unittest.main(verbosity=2)
