"""Testes do indicador de gravação na bandeja (#14): pulso do ícone REC + tick do tooltip.

Não sobe a bandeja real (pystray): _icon_image é PIL puro, e _tick_tray roda com o app
e o tray stubados (ScribaApp.__new__ sem __init__, como em test_relaunch)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.tray import _icon_image  # noqa: E402


def _alpha_sum(img) -> int:
    return sum(img.getchannel("A").getdata())


class IconImageTests(unittest.TestCase):
    def test_dim_apaga_o_icone_rec(self):
        # gravando + dim = variante apagada -> menos alpha que a normal (pulso)
        self.assertLess(_alpha_sum(_icon_image(True, dim=True)), _alpha_sum(_icon_image(True)))

    def test_dim_ignorado_quando_nao_grava(self):
        # fora da gravação não há o que pulsar: dim não muda o ícone
        self.assertEqual(list(_icon_image(False).getdata()), list(_icon_image(False, dim=True).getdata()))


class TickTrayTests(unittest.TestCase):
    def _app(self):
        from scriba.main import ScribaApp

        app = ScribaApp.__new__(ScribaApp)  # sem __init__: nada de Tk/threads
        app.tray = mock.Mock()
        app.root = mock.Mock()
        app.stop_event = mock.Mock()
        app.stop_event.is_set.return_value = False
        app.status_text = mock.Mock(return_value="ScribaDev — gravando 0:05 (Teams)")
        return app

    def test_pulso_alterna_e_reagenda(self):
        app = self._app()
        app.is_recording = mock.Mock(return_value=True)

        app._tick_tray()
        self.assertTrue(app._tray_pulse)  # 1º tick: apaga
        app.tray.set_recording.assert_called_with(True, "ScribaDev — gravando 0:05 (Teams)", dim=True)
        app.root.after.assert_called_with(700, app._tick_tray)

        app._tick_tray()
        self.assertFalse(app._tray_pulse)  # 2º tick: acende de novo

    def test_nao_faz_nada_sem_gravacao(self):
        app = self._app()
        app.is_recording = mock.Mock(return_value=False)
        app._tick_tray()
        app.tray.set_recording.assert_not_called()
        app.root.after.assert_not_called()


if __name__ == "__main__":
    unittest.main()
