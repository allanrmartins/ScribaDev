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


class CorrecaoEmPedacosTests(unittest.TestCase):
    def test_marca_onde_trocou_e_mantem_o_resto(self):
        from scriba import vocab_fix

        pedacos = vocab_fix.corrigir_trechos("abri a S 80 e o smartformes", ["SE80", "SmartForms"])
        self.assertEqual(pedacos, [("abri a", None), ("SE80", "S 80"), ("e o", None),
                                   ("SmartForms", "smartformes")])
        # juntando os pedaços sai exatamente o que o processamento grava na nota
        self.assertEqual(" ".join(t for t, _ in pedacos),
                         vocab_fix.corrigir("abri a S 80 e o smartformes", ["SE80", "SmartForms"])[0])

    def test_sem_correcao_um_pedaco_so(self):
        from scriba import vocab_fix

        self.assertEqual(vocab_fix.corrigir_trechos("tudo certo", ["SE80"]), [("tudo certo", None)])
        self.assertEqual(vocab_fix.corrigir_trechos("   ", ["SE80"]), [])

    def test_leitor_aplica_o_corretor_e_sobrevive_a_falha(self):
        d = Path(tempfile.mkdtemp(prefix="scriba_live_"))
        (d / "mic.live.jsonl").write_text(_linha(1.0, "na S 80"), encoding="utf-8")
        from scriba import vocab_fix

        tail = LiveTail(d, corretor=lambda t: vocab_fix.corrigir_trechos(t, ["SE80"]))
        tail.poll()
        self.assertTrue(tail.lines[0].corrigido)
        ruim = LiveTail(d, corretor=lambda t: 1 / 0)
        ruim.poll()
        self.assertEqual(ruim.lines[0].pieces, (("na S 80", None),))


class CanalDoProvisorioTests(unittest.TestCase):
    def test_le_o_pipe_e_limpa_quando_fixa(self):
        """O texto provisório chega por pipe (memória) e some quando o trecho fixa."""
        import threading

        from scriba.stt_apple import LiveSession

        sess = LiveSession.__new__(LiveSession)   # sem subir helper: só o leitor do canal
        sess._lock = threading.Lock()
        sess._partials = {}
        r, w = os.pipe()
        th = threading.Thread(target=sess._read_partials, args=("mic", r))
        th.start()
        os.write(w, json.dumps({"start": 3.0, "text": "estou falando"}).encode() + b"\n")
        os.write(w, b"lixo que nao e json\n")
        os.write(w, json.dumps({"start": 3.0, "text": "estou falando agora"}).encode() + b"\n")
        import time

        fim = time.monotonic() + 2
        while sess.partials().get("mic", (0, ""))[1] != "estou falando agora" and time.monotonic() < fim:
            time.sleep(0.01)
        self.assertEqual(sess.partials(), {"mic": (3.0, "estou falando agora")})
        os.write(w, json.dumps({"start": 3.0, "text": ""}).encode() + b"\n")   # fixou
        fim = time.monotonic() + 2
        while sess.partials() and time.monotonic() < fim:
            time.sleep(0.01)
        self.assertEqual(sess.partials(), {})
        os.close(w)
        th.join(2)
        self.assertFalse(th.is_alive())


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
            def live_partials(self): return dict(self.partials)
            def ui(self, fn): pump.go.emit(fn)

        app = _App()
        app.folder = folder
        app.partials = {}
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
        app.partials = {"loopback": (0.5, "Vamos com")}   # frase ainda sendo dita
        win._poll_transcript()
        self.assertIn("Vamos com", win._lt_view.toPlainText())
        app.partials = {}
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


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 ausente")
class CorrecaoNaTelaTests(PainelTests):
    def test_riscado_some_depois_do_tempo(self):
        import dataclasses
        import time

        from scriba import vocab_fix

        pasta = Path(tempfile.mkdtemp(prefix="scriba_live_"))
        win = self._win(self._app(pasta))
        self._espera_leitura(win)   # cria o leitor (pasta ainda vazia: nada a corrigir)
        win._lt_tail._corretor = lambda t: vocab_fix.corrigir_trechos(t, ["SE80"])
        (pasta / "mic.live.jsonl").write_text(_linha(2.0, "abre a S 80"), encoding="utf-8")
        self._espera_leitura(win)
        html = win._lt_view.toHtml()
        self.assertIn("S 80", win._lt_view.toPlainText())   # riscado ainda à vista
        self.assertIn("line-through", html)
        self.assertTrue(win._lt_fading)
        win._lt_tail.lines = [dataclasses.replace(ln, chegou=time.monotonic() - 60)
                              for ln in win._lt_tail.lines]
        win._render_transcript(force_bottom=True)
        self.assertNotIn("S 80", win._lt_view.toPlainText())
        self.assertIn("abre a SE80", win._lt_view.toPlainText())
        self.assertFalse(win._lt_fading)
