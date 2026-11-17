"""Transcrição ao vivo na capa: leitor incremental dos .live.jsonl e o painel do card."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from scriba.live_tail import LiveTail  # noqa: E402


def _linha(start, text):
    return json.dumps({"start": start, "end": start + 5, "text": text}) + "\n"


class LiveTailTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="scriba_live_"))
        self.tail = LiveTail(self.dir)

    def _append(self, stream, texto):
        with open(self.dir / f"{stream}.live.jsonl", "a", encoding="utf-8") as f:
            f.write(texto)

    def test_sem_arquivos_nao_ha_nada(self):
        self.assertFalse(self.tail.poll())
        self.assertEqual(self.tail.lines, [])

    def test_le_so_o_que_chegou_e_ordena_pelo_inicio(self):
        self._append("loopback", _linha(10.0, "Pode compartilhar a tela?"))
        self._append("mic", _linha(3.0, "Bom dia."))
        self.assertTrue(self.tail.poll())
        self.assertEqual([(ln.stream, ln.text) for ln in self.tail.lines],
                         [("mic", "Bom dia."), ("loopback", "Pode compartilhar a tela?")])
        self.assertFalse(self.tail.poll())   # nada novo
        self._append("mic", _linha(6.0, "Compartilhando."))
        self.assertTrue(self.tail.poll())
        self.assertEqual([ln.start for ln in self.tail.lines], [3.0, 6.0, 10.0])

    def test_linha_pela_metade_espera_o_resto(self):
        inteira = _linha(1.0, "Trecho que chega em dois pedaços.")
        self._append("mic", inteira[:20])
        self.assertFalse(self.tail.poll())
        self._append("mic", inteira[20:])
        self.assertTrue(self.tail.poll())
        self.assertEqual(self.tail.lines[0].text, "Trecho que chega em dois pedaços.")

    def test_fim_do_helper_e_texto_vazio_nao_viram_trecho(self):
        self._append("mic", json.dumps({"done": True}) + "\n" + _linha(2.0, "   "))
        self.assertFalse(self.tail.poll())

    def test_arquivo_recriado_recomeca(self):
        self._append("mic", _linha(1.0, "Primeira versão do arquivo, mais comprida."))
        self.tail.poll()
        (self.dir / "mic.live.jsonl").write_text(_linha(1.0, "Nova."), encoding="utf-8")
        self.assertTrue(self.tail.poll())
        self.assertEqual([ln.text for ln in self.tail.lines], ["Nova."])


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 ausente")
class PainelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.qapp = QApplication.instance() or QApplication([])

    def _win(self, app):
        from unittest import mock

        from scriba import util
        from scriba.qt.main_window import MainWindow

        d = Path(tempfile.mkdtemp(prefix="scriba_state_"))
        p = mock.patch.object(util, "STATE_PATH", d / "state.json")
        p.start()
        self.addCleanup(p.stop)
        win = MainWindow(app)
        win._lt_timer.stop()
        win._live_timer.stop()
        win._tick_timer.stop()
        win.show()
        self.addCleanup(win.close)
        return win

    def _app(self, folder):
        from PySide6.QtCore import QObject, Signal

        class _Pump(QObject):   # como o app real: a leitura roda fora da GUI e marshala
            go = Signal(object)

        pump = _Pump()
        pump.go.connect(lambda fn: fn())
        self._pump = pump

        class _App:
            cfg = None
            call_active = True
            update_news = None

            def is_recording(self): return True
            def current_call_app(self): return "Teams"
            def recording_duration(self): return 30.0
            def call_duration(self): return 30.0
            def live_transcript_folder(self): return self.folder
            def ui(self, fn): pump.go.emit(fn)

        app = _App()
        app.folder = folder
        return app

    def _espera_leitura(self, win):
        import time

        win._poll_transcript()
        fim = time.monotonic() + 3
        while win._lt_busy and time.monotonic() < fim:
            time.sleep(0.01)
        self.qapp.processEvents()

    def test_app_sem_ao_vivo_nao_mostra_o_painel(self):
        win = self._win(self._app(None))
        win._poll_transcript()
        self.assertTrue(win._lt_panel.isHidden())

    def test_app_antigo_sem_o_metodo_nao_quebra(self):
        app = self._app(None)
        del type(app).live_transcript_folder
        win = self._win(app)
        win._poll_transcript()
        self.assertTrue(win._lt_panel.isHidden())

    def test_mostra_as_falas_e_some_ao_parar(self):
        pasta = Path(tempfile.mkdtemp(prefix="scriba_live_"))
        app = self._app(pasta)
        win = self._win(app)
        altura = win.height()
        self._espera_leitura(win)
        self.assertFalse(win._lt_panel.isHidden())
        self.assertIn("Ouvindo", win._lt_view.toPlainText())
        (pasta / "mic.live.jsonl").write_text(_linha(4.0, "Pode seguir com o roteiro."), encoding="utf-8")
        (pasta / "loopback.live.jsonl").write_text(_linha(1.0, "Vamos começar."), encoding="utf-8")
        self._espera_leitura(win)
        texto = win._lt_view.toPlainText()
        self.assertLess(texto.index("Vamos começar."), texto.index("Pode seguir com o roteiro."))
        self.assertIn("Você", texto)
        self.assertIn("Outros", texto)
        app.folder = None   # gravação acabou
        win._poll_transcript()
        self.assertTrue(win._lt_panel.isHidden())
        self.assertEqual(win.height(), altura)   # alongou e voltou


if __name__ == "__main__":
    unittest.main()
