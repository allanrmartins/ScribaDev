"""Fim de call do detector x gravação manual (2026-10-08).

O Teams soltava o mic com o usuário calado e o detector dava a call por
encerrada: a gravação parava no meio da reunião, inclusive a que o usuário tinha
iniciado à mão no ⏺. Gravação manual só termina no ■ (ou no × da pílula).

ScribaApp via __new__ (sem __init__): nada de Qt, áudio ou detector real.
"""

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.main import ScribaApp  # noqa: E402


class CallEndedTests(unittest.TestCase):
    def _app(self, source: str):
        app = ScribaApp.__new__(ScribaApp)
        app.rec_lock = threading.Lock()
        app.rec = object()  # gravação em andamento
        app.rec_source = source
        app.call_active = True
        app.call_started_at = 1.0
        app.pill = mock.Mock()
        app.is_recording = lambda: app.rec is not None
        app.stop_recording = mock.Mock()
        app._toast = mock.Mock()
        app.ui = lambda fn: fn()
        app._hide_pill = mock.Mock()
        return app

    def test_gravacao_automatica_para_no_fim_da_call(self):
        app = self._app("auto")
        app._on_call_ended()
        app.stop_recording.assert_called_once_with()
        self.assertFalse(app.call_active)

    def test_gravacao_manual_segue_no_fim_da_call(self):
        app = self._app("manual")
        with self.assertLogs("scriba", "INFO") as cm:
            app._on_call_ended()
        app.stop_recording.assert_not_called()
        self.assertFalse(app.call_active)
        self.assertIn("a gravação é manual - segue até o ■", "\n".join(cm.output))
        app._toast.assert_called_once()  # avisa: o ■ é com o usuário

    def test_pilula_nao_diz_finalizando_na_gravacao_manual(self):
        app = self._app("manual")
        app._pill_finishing()
        app.pill.set_status.assert_not_called()

    def test_pilula_diz_finalizando_na_gravacao_automatica(self):
        app = self._app("auto")
        app._pill_finishing()
        app.pill.set_status.assert_called_once_with("finalizando…")

    def test_sem_gravacao_esconde_a_pilula(self):
        app = self._app("manual")
        app.rec = None
        app._on_call_ended()
        app.stop_recording.assert_not_called()
        app._hide_pill.assert_called_once()


if __name__ == "__main__":
    unittest.main()
