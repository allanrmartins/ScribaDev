"""#102: costuras de SO degradam com graça no POSIX (stubs do Marco 1, épico #104).

Os guards checam sys.platform em TEMPO DE CHAMADA, então dá para exercitar o ramo
POSIX aqui no Windows patchando sys.platform — sem tocar pyaudiowpatch/winreg/Win32.
(Exceção: wintitles decide no import; o ramo POSIX dele só roda no CI Linux, #103.)
"""

import sys
import unittest
from unittest import mock

from scriba import autostart, detector, hotkey, recorder, shortcuts


def _linux():
    return mock.patch.object(sys, "platform", "linux")


class TestRecorderStubs(unittest.TestCase):
    def test_record_for_degrada_com_mensagem(self):
        with _linux(), mock.patch("builtins.print") as p:
            self.assertEqual(recorder.record_for(5, show_ui=False), 1)
        self.assertIn("não suportada neste SO", p.call_args[0][0])

    def test_list_devices_degrada_com_mensagem(self):
        with _linux(), mock.patch("builtins.print") as p:
            self.assertEqual(recorder.list_devices(), 1)
        self.assertIn("não suportada neste SO", p.call_args[0][0])

    def test_recording_levanta_erro_claro(self):
        with _linux(), self.assertRaisesRegex(RuntimeError, "não suportada neste SO"):
            recorder.Recording(mock.Mock())


class TestDetectorStubs(unittest.TestCase):
    def test_debug_loop_degrada_com_mensagem(self):
        with _linux(), mock.patch("builtins.print") as p:
            self.assertEqual(detector.debug_loop(), 1)
        self.assertIn("não suportada neste SO", p.call_args[0][0])

    def test_thread_do_detector_sai_logando(self):
        det = detector.Detector.__new__(detector.Detector)  # sem __init__: só o guard
        stop = mock.Mock()
        with _linux(), self.assertLogs("scriba.detector", level="INFO") as logs:
            det.run(stop)
        self.assertTrue(any("não suportada neste SO" in m for m in logs.output))
        stop.is_set.assert_not_called()  # saiu antes do loop


class TestHotkeyAutostartShortcuts(unittest.TestCase):
    def test_hotkey_start_devolve_false_logando(self):
        hk = hotkey.GlobalHotkey("ctrl+alt+r", lambda: None)
        with _linux(), self.assertLogs("scriba.hotkey", level="INFO"):
            self.assertFalse(hk.start())

    def test_autostart_is_enabled_false(self):
        with _linux():
            self.assertFalse(autostart.is_enabled())

    def test_autostart_set_degrada_com_mensagem(self):
        with _linux(), mock.patch("builtins.print") as p:
            self.assertEqual(autostart.set_autostart(True), 1)
        self.assertIn("não suportado neste SO", p.call_args[0][0])

    def test_shortcuts_degrada_com_mensagem(self):
        with _linux(), mock.patch("builtins.print") as p:
            self.assertEqual(shortcuts.create_shortcuts(), 1)
        self.assertIn("não suportados neste SO", p.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
