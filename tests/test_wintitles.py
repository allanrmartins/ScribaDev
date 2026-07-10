"""Testes de scriba.wintitles (#114): enumeração de janelas com prazo.

EnumWindows pode bloquear indefinidamente quando um processo com janela top-level
está travado (aconteceu em produção: pendurou o detector dentro do início da
gravação e congelou o app). window_titles roda a enumeração numa thread com
timeout; estes testes cobrem o contrato — timeout devolve [], enumeração presa
não empilha novas threads, e o caminho feliz passa o resultado adiante.
"""

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import wintitles  # noqa: E402


class WindowTitlesTimeoutTests(unittest.TestCase):
    def setUp(self):
        self._orig = wintitles._enum_titles
        self._release = threading.Event()  # solta a enumeração "presa" no teardown

    def tearDown(self):
        self._release.set()
        wintitles._enum_titles = self._orig
        # espera as threads abandonadas morrerem p/ não vazar entre testes
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            with wintitles._stuck_lock:
                wintitles._stuck[:] = [t for t in wintitles._stuck if t.is_alive()]
                if not wintitles._stuck:
                    break
            time.sleep(0.01)

    def test_caminho_feliz_passa_o_resultado(self):
        wintitles._enum_titles = lambda names: ["Reunião - Google Meet"]
        self.assertEqual(wintitles.window_titles({"chrome.exe"}), ["Reunião - Google Meet"])

    def test_enumeracao_presa_devolve_vazio_no_prazo(self):
        def presa(names):
            self._release.wait(10)  # simula EnumWindows pendurado
            return ["tarde demais"]

        wintitles._enum_titles = presa
        t0 = time.monotonic()
        self.assertEqual(wintitles.window_titles({"chrome.exe"}, timeout_s=0.2), [])
        self.assertLess(time.monotonic() - t0, 2.0, "timeout não respeitado")

    def test_presa_anterior_bloqueia_novas_enumeracoes(self):
        chamadas = []

        def presa(names):
            chamadas.append(1)
            self._release.wait(10)
            return []

        wintitles._enum_titles = presa
        self.assertEqual(wintitles.window_titles({"chrome.exe"}, timeout_s=0.2), [])
        # com a anterior ainda presa, nem tenta outra (não empilha thread por poll)
        self.assertEqual(wintitles.window_titles({"chrome.exe"}, timeout_s=0.2), [])
        self.assertEqual(len(chamadas), 1)

    def test_enumeracao_que_explode_devolve_vazio(self):
        def boom(names):
            raise OSError("user32 indisponível")

        wintitles._enum_titles = boom
        self.assertEqual(wintitles.window_titles({"chrome.exe"}), [])

    def test_apos_presa_morrer_volta_a_enumerar(self):
        def presa(names):
            self._release.wait(10)
            return []

        wintitles._enum_titles = presa
        wintitles.window_titles({"chrome.exe"}, timeout_s=0.2)
        self._release.set()
        time.sleep(0.05)  # deixa a thread presa terminar
        wintitles._enum_titles = lambda names: ["viva de novo"]
        self.assertEqual(wintitles.window_titles({"chrome.exe"}), ["viva de novo"])


class TituloAssincronoMetaTests(unittest.TestCase):
    """Recording._capture_title_async (#114): o título regrava o meta enquanto a
    gravação está ativa e é DESCARTADO se chegar depois do stop (não pode regravar
    'recording' por cima do meta final). Usa uma instância oca de Recording — sem
    abrir áudio real."""

    def _hollow_recording(self, tmp: Path):
        import json
        from types import SimpleNamespace

        from scriba.recorder import Recording

        rec = Recording.__new__(Recording)
        rec.folder = tmp
        rec.started_at = __import__("datetime").datetime(2026, 7, 10, 9, 0, 0)
        rec.meeting_title = ""
        rec._meta_lock = threading.Lock()
        rec._meta_final = False
        stream = SimpleNamespace(
            wav=SimpleNamespace(path=Path("mic.wav"), frames_written=16000),
            device_name="fake", rate=16000, channels=1, offset_seconds=0.0,
        )
        rec.mic = stream
        rec.loopback = SimpleNamespace(
            wav=SimpleNamespace(path=Path("loopback.wav"), frames_written=16000),
            device_name="fake", rate=16000, channels=1, offset_seconds=0.0,
        )
        rec._read_meta = lambda: json.loads((tmp / "meta.json").read_text(encoding="utf-8"))
        return rec

    def test_titulo_regrava_meta_enquanto_grava(self):
        import json
        import tempfile

        from scriba import detector

        tmp = Path(tempfile.mkdtemp(prefix="scriba_title_"))
        rec = self._hollow_recording(tmp)
        rec._write_meta("recording")
        orig = detector.capture_meeting_title
        detector.capture_meeting_title = lambda cfg: "Reunião de teste"
        try:
            rec._capture_title_async(None)
        finally:
            detector.capture_meeting_title = orig
        meta = json.loads((tmp / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta.get("meeting_title"), "Reunião de teste")
        self.assertEqual(meta.get("status"), "recording")

    def test_titulo_tardio_nao_regrava_meta_final(self):
        import json
        import tempfile

        from scriba import detector

        tmp = Path(tempfile.mkdtemp(prefix="scriba_title_"))
        rec = self._hollow_recording(tmp)
        # simula o stop(): meta final gravado e trancado
        with rec._meta_lock:
            rec._meta_final = True
            rec._write_meta("recorded", {"duration_seconds": 12.3})
        orig = detector.capture_meeting_title
        detector.capture_meeting_title = lambda cfg: "chegou tarde"
        try:
            rec._capture_title_async(None)
        finally:
            detector.capture_meeting_title = orig
        meta = json.loads((tmp / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta.get("status"), "recorded", "título tardio regrediu o status")
        self.assertNotIn("meeting_title", meta)


if __name__ == "__main__":
    unittest.main()
