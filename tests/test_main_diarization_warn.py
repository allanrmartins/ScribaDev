"""Vozes ligadas sem os componentes instalados (2026-10-20).

O setup do Mac pulou o download de torch/pyannote com a diarização ligada na
config: toda reunião saía sem separar as vozes e só o scriba.log sabia. Agora o
"falta o extra" vira toast - uma vez por sessão.

ScribaApp via __new__ (sem __init__): nada de Qt, áudio ou detector real.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba.main import ScribaApp  # noqa: E402

_FALTA = "dependências ausentes — falta o extra [diarization] (No module named 'torch')"


class DiarizationWarnTests(unittest.TestCase):
    def _app(self):
        app = ScribaApp.__new__(ScribaApp)
        app._voices_missing_toasted = False
        app._toast = mock.Mock()
        return app

    def test_componentes_ausentes_avisam_uma_vez_por_sessao(self):
        app = self._app()
        with self.assertLogs("scriba", "WARNING") as cm:
            app._warn_diarization_error(Path("10-59"), _FALTA)
            app._warn_diarization_error(Path("13-30"), _FALTA)
        self.assertEqual(len(cm.records), 2)   # o log continua valendo a cada reunião
        app._toast.assert_called_once()
        self.assertIn("Baixar componentes", app._toast.call_args.args[1])

    def test_outras_falhas_ficam_so_no_log(self):
        """Token ausente, VRAM, import interno quebrado: têm o próprio caminho de
        diagnóstico - o toast é só p/ o caso "nada instalado"."""
        app = self._app()
        with self.assertLogs("scriba", "WARNING"):
            app._warn_diarization_error(
                Path("10-59"), "habilitada sem token Hugging Face — configure em Configurações → Gravação → Diarização")
        app._toast.assert_not_called()


if __name__ == "__main__":
    unittest.main()
