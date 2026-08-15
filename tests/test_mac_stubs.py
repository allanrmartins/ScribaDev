"""M1 do port macOS (épico #104, docs/port-mac.md): tema por SO, textos do template
da config, segredos no Keychain e so_nome.

Mesma convenção do test_stubs_posix: os guards checam sys.platform em TEMPO DE
CHAMADA, então os ramos darwin rodam em qualquer host patchando sys.platform.
Exceção: os hints _H da config são computados no IMPORT (como o wintitles) — os
testes de template recarregam o módulo sob o sys.platform desejado.
"""

import importlib
import subprocess as subprocess_mod
import sys
import unittest
from unittest import mock

from scriba import config, util
from scriba.qt import theme


def _darwin():
    return mock.patch.object(sys, "platform", "darwin")


def _linux():
    return mock.patch.object(sys, "platform", "linux")


def _sem_qapp():
    from PySide6.QtGui import QGuiApplication

    return mock.patch.object(QGuiApplication, "instance", staticmethod(lambda: None))


class TestTemaDarwin(unittest.TestCase):
    def test_defaults_read_modo_escuro(self):
        # AppleInterfaceStyle existe (rc 0) = modo escuro
        with _darwin(), _sem_qapp(), mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0)
            self.assertTrue(theme._os_prefers_dark())
        self.assertEqual(run.call_args[0][0][:3], ["defaults", "read", "-g"])

    def test_defaults_read_modo_claro(self):
        # a chave só existe no escuro: rc != 0 = modo claro (era o bug: caía no True)
        with _darwin(), _sem_qapp(), mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1)
            self.assertFalse(theme._os_prefers_dark())

    def test_falha_total_mantem_escuro(self):
        with _darwin(), _sem_qapp(), mock.patch("subprocess.run", side_effect=OSError("boom")):
            self.assertTrue(theme._os_prefers_dark())


class TestTemplateConfigPorSO(unittest.TestCase):
    """DEFAULT_CONFIG e o template do save() falam a língua do SO. Os hints são
    resolvidos no import ⇒ reload sob sys.platform patchado, com restauração."""

    def _render(self, platform: str) -> str:
        with mock.patch.object(sys, "platform", platform):
            importlib.reload(config)
            return config.DEFAULT_CONFIG

    def tearDown(self):
        importlib.reload(config)  # restaura os hints do host real

    def test_darwin_sem_caminhos_windows(self):
        text = self._render("darwin")
        self.assertIn("Application Support/ScribaDev/Notas", text)
        self.assertIn("Application Support/ScribaDev/gravacoes", text)
        self.assertIn("fica no Keychain", text)
        self.assertNotIn("%LOCALAPPDATA%", text)
        self.assertNotIn("C:\\temp", text)
        self.assertNotIn("do Windows", text)

    def test_win32_texto_historico_intacto(self):
        text = self._render("win32")
        self.assertIn(r"Vazio = %LOCALAPPDATA%\ScribaDev\Notas", text)
        self.assertIn(r"Vazio = C:\temp\scribadev\gravacoes", text)
        self.assertIn("registro de uso do microfone do\n# Windows", text)
        self.assertIn("fica cifrada (DPAPI)", text)
        self.assertNotIn("Keychain", text)

    def test_template_e_toml_valido_nos_tres_sos(self):
        import tomllib

        for platform in ("win32", "darwin", "linux"):
            tomllib.loads(self._render(platform))

    def test_save_template_darwin(self):
        import tomllib

        text = self._render("darwin")
        cfg = config._build(tomllib.loads(text))
        rendered = {}

        def capture(path, content):
            rendered["text"] = content

        with mock.patch.object(sys, "platform", "darwin"), \
                mock.patch.object(config.util, "ensure_app_dirs"), \
                mock.patch.object(config.util, "atomic_write_text", capture), \
                mock.patch.object(config.util.CONFIG_PATH.__class__, "exists", lambda self: False):
            config.save(cfg)
        self.assertIn("Application Support/ScribaDev/Notas", rendered["text"])
        self.assertNotIn("%LOCALAPPDATA%", rendered["text"])
        self.assertNotIn("do Windows", rendered["text"])


class TestKeychain(unittest.TestCase):
    def test_fora_do_darwin_devolve_none(self):
        with _linux():
            self.assertIsNone(util.keychain_store("conta", "segredo"))
            self.assertIsNone(util.keychain_lookup("keychain:conta"))
            self.assertFalse(util.keychain_ok())

    def test_store_segredo_vai_por_stdin_nunca_argv(self):
        with _darwin(), mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0)
            token = util.keychain_store("summary.openai_api_key", "sk-SEGREDO123")
        self.assertEqual(token, "keychain:summary.openai_api_key")
        argv = run.call_args[0][0]
        self.assertEqual(argv, ["/usr/bin/security", "-i"])
        self.assertNotIn("sk-SEGREDO123", " ".join(argv))
        self.assertIn(b"sk-SEGREDO123", run.call_args[1]["input"])

    def test_store_recusa_texto_arriscado(self):
        # aspas/controle poderiam quebrar o tokenizer do security -i: degrada p/ plaintext
        with _darwin(), mock.patch("subprocess.run") as run:
            self.assertIsNone(util.keychain_store("conta", 'se"gredo'))
            self.assertIsNone(util.keychain_store("conta", "seg\nredo"))
            self.assertIsNone(util.keychain_store('con"ta', "segredo"))
        run.assert_not_called()

    def test_store_falha_devolve_none(self):
        with _darwin(), mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=51)  # chaveiro trancado etc.
            self.assertIsNone(util.keychain_store("conta", "segredo"))

    def test_lookup_roundtrip(self):
        with _darwin(), mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=b"sk-SEGREDO123\n")
            self.assertEqual(util.keychain_lookup("keychain:conta"), "sk-SEGREDO123")
            run.return_value = mock.Mock(returncode=44)  # item não existe
            self.assertIsNone(util.keychain_lookup("keychain:conta"))

    def test_maybe_protect_darwin_usa_keychain(self):
        with _darwin(), mock.patch.object(config.util, "keychain_store",
                                          return_value="keychain:x") as ks:
            self.assertEqual(config._maybe_protect("segredo", "x"), "keychain:x")
        ks.assert_called_once_with("x", "segredo")

    def test_maybe_protect_darwin_sem_keychain_degrada_plaintext(self):
        with _darwin(), mock.patch.object(config.util, "keychain_store", return_value=None):
            self.assertEqual(config._maybe_protect("segredo", "x"), "segredo")

    def test_maybe_protect_token_existente_passa_direto(self):
        with _darwin():
            self.assertEqual(config._maybe_protect("keychain:x", "x"), "keychain:x")
            self.assertEqual(config._maybe_protect("dpapi:abc", "x"), "dpapi:abc")
            self.assertEqual(config._maybe_protect("", "x"), "")

    def test_maybe_unprotect_resolve_keychain(self):
        with mock.patch.object(config.util, "keychain_lookup", return_value="segredo"):
            self.assertEqual(config._maybe_unprotect("keychain:x"), "segredo")
        with mock.patch.object(config.util, "keychain_lookup", return_value=None):
            # item removido/chaveiro trancado: chave inutilizável — mesma regra da DPAPI
            self.assertEqual(config._maybe_unprotect("keychain:x"), "")


@unittest.skipUnless(sys.platform == "darwin", "importa recorder_mac (CoreAudio de verdade)")
class TestRecorderDispatchDarwin(unittest.TestCase):
    """M3: os seams do recorder despacham p/ o backend mac. Os backends em si são
    mockados — a captura real é o checklist manual do docs/port-mac.md."""

    def test_level_probe_factory_darwin(self):
        with mock.patch("scriba.recorder_mac.LevelProbeMac", return_value="probe-mac"):
            from scriba import recorder

            self.assertEqual(recorder.LevelProbe(mock.Mock()), "probe-mac")

    def test_list_devices_delegado(self):
        from scriba import recorder

        with mock.patch("scriba.recorder_mac.list_devices_text", return_value=0) as f:
            self.assertEqual(recorder.list_devices(), 0)
        f.assert_called_once()

    def test_probe_list_formato(self):
        # mesmo contrato JSON do audioprobe do Windows: mics/loopbacks/defaults
        from scriba import recorder_mac

        fake_devs = [
            {"name": "Mic Interno", "max_input_channels": 1, "max_output_channels": 0,
             "default_samplerate": 48000.0},
            {"name": "Alto-falantes", "max_input_channels": 0, "max_output_channels": 2,
             "default_samplerate": 48000.0},
            {"name": "ScribaTap-abc", "max_input_channels": 2, "max_output_channels": 0,
             "default_samplerate": 48000.0},
        ]

        class FakeSd:
            class default:
                device = (0, 1)

            @staticmethod
            def query_devices(i=None):
                return fake_devs if i is None else fake_devs[i]

        with mock.patch.object(recorder_mac, "_sd", return_value=FakeSd):
            out = recorder_mac.probe_list()
        self.assertEqual(out["mics"], ["Mic Interno"])            # sem o aggregate do tap
        self.assertEqual(out["loopbacks"], ["Alto-falantes"])     # saídas = alvos de clock
        self.assertEqual(out["default_mic"], "Mic Interno")
        self.assertEqual(out["default_loopback"], "Alto-falantes")

    def test_pick_mic_substring_e_default(self):
        from scriba import recorder_mac

        fake_devs = [
            {"name": "Mic Interno", "max_input_channels": 1, "max_output_channels": 0,
             "default_samplerate": 48000.0},
            {"name": "Headset USB", "max_input_channels": 1, "max_output_channels": 2,
             "default_samplerate": 44100.0},
        ]

        class FakeSd:
            class default:
                device = (0, None)

            @staticmethod
            def query_devices(i=None):
                return fake_devs if i is None else fake_devs[i]

        with mock.patch.object(recorder_mac, "_sd", return_value=FakeSd):
            eng = mock.Mock()
            self.assertEqual(recorder_mac.pick_mic(mock.Mock(mic_device="headset"), eng)["name"],
                             "Headset USB")
            self.assertEqual(recorder_mac.pick_mic(mock.Mock(mic_device=""), eng)["name"],
                             "Mic Interno")


class TestBrowserKeyMatch(unittest.TestCase):
    """M4: match de navegador por SO (função pura — roda em qualquer host)."""

    def test_win32_exige_exe_exato(self):
        from scriba import detector

        with mock.patch.object(sys, "platform", "win32"):
            self.assertTrue(detector._browser_key_match("C:#path#msedge.exe", "msedge"))
            # o webview embutido do Teams/Outlook NÃO pode casar
            self.assertFalse(detector._browser_key_match("C:#path#msedgewebview2.exe", "msedge"))

    def test_darwin_usa_tabela_de_bundles(self):
        from scriba import detector

        with _darwin():
            self.assertTrue(detector._browser_key_match("com.google.chrome", "chrome"))
            # o áudio do Chrome roda num helper — prefixo tem que casar
            self.assertTrue(detector._browser_key_match("com.google.chrome.helper", "chrome"))
            self.assertTrue(detector._browser_key_match("com.microsoft.edgemac", "msedge"))
            self.assertFalse(detector._browser_key_match("com.microsoft.teams2", "msedge"))
            self.assertFalse(detector._browser_key_match("us.zoom.xos", "chrome"))


class TestMacTitlesMapa(unittest.TestCase):
    def test_exe_para_bundle(self):
        from scriba import mactitles

        pref = mactitles._prefixes_for({"chrome.exe", "ms-teams.exe"})
        self.assertIn("com.google.chrome", pref)
        self.assertIn("com.microsoft.teams", pref)


@unittest.skipUnless(sys.platform == "darwin", "micusage_mac carrega o CoreAudio de verdade")
class TestMicusageSnapshot(unittest.TestCase):
    """M4: síntese de carimbos FILETIME a partir das transições do IsRunningInput."""

    def setUp(self):
        from scriba import micusage_mac

        self.mu = micusage_mac
        self.mu.reset()

    def tearDown(self):
        self.mu.reset()

    def _snap(self, procs):
        with mock.patch.object(self.mu, "_process_inputs", return_value=procs):
            return {k: (start, stop) for k, start, stop in self.mu.snapshot()}

    def test_abre_fecha_reabre(self):
        s1 = self._snap([(10, "com.microsoft.teams2", True)])
        start1, stop1 = s1["com.microsoft.teams2"]
        self.assertGreater(start1, 0)
        self.assertEqual(stop1, 0)  # mic aberto = análogo do LastUsedTimeStop == 0

        s2 = self._snap([(10, "com.microsoft.teams2", False)])
        start2, stop2 = s2["com.microsoft.teams2"]
        self.assertEqual(start2, start1)   # fechar não mexe no start
        self.assertGreaterEqual(stop2, start1)

        s3 = self._snap([(10, "com.microsoft.teams2", True)])
        start3, stop3 = s3["com.microsoft.teams2"]
        self.assertGreaterEqual(start3, stop2)  # REABRIR reescreve o start (#34)
        self.assertEqual(stop3, 0)

    def test_processo_morto_fecha_sessao(self):
        self._snap([(10, "us.zoom.xos", True)])
        s = self._snap([])  # o processo sumiu com o mic aberto
        self.assertNotEqual(s["us.zoom.xos"][1], 0)

    def test_helpers_do_mesmo_bundle_fundem(self):
        s = self._snap([(10, "com.google.Chrome.helper", True),
                        (11, "com.google.Chrome.helper", False)])
        self.assertEqual(list(s), ["com.google.chrome.helper"])
        self.assertEqual(s["com.google.chrome.helper"][1], 0)  # OR: um aberto basta


class TestMakeTranscriberMlx(unittest.TestCase):
    """M5: a fábrica escolhe o MLX só em darwin+arm64 (e nunca com force_cpu)."""

    def _make(self, platform_name, machine, engine="local", force_cpu=False, mlx_ok=True):
        from scriba import transcription

        cfg = mock.Mock(engine=engine, model="large-v3-turbo", language="pt",
                        hotwords="", cpu_threads=0)
        with mock.patch.object(sys, "platform", platform_name), \
                mock.patch("platform.machine", return_value=machine), \
                mock.patch("scriba.stt_mlx.mlx_disponivel", return_value=mlx_ok):
            return transcription.make_transcriber(cfg, force_cpu=force_cpu)

    def test_darwin_arm64_local_vira_mlx(self):
        from scriba.stt_mlx import MlxWhisperProvider

        self.assertIsInstance(self._make("darwin", "arm64"), MlxWhisperProvider)

    def test_sem_mlx_instalado_cai_no_faster_whisper(self):
        from scriba.transcriber import Transcriber

        self.assertIsInstance(self._make("darwin", "arm64", mlx_ok=False), Transcriber)

    def test_engine_mlx_explicito_forca(self):
        from scriba.stt_mlx import MlxWhisperProvider

        self.assertIsInstance(self._make("darwin", "arm64", engine="mlx", mlx_ok=False),
                              MlxWhisperProvider)

    def test_force_cpu_pula_mlx(self):
        from scriba.transcriber import Transcriber

        self.assertIsInstance(self._make("darwin", "arm64", force_cpu=True), Transcriber)

    def test_fora_do_mac_segue_local(self):
        from scriba.transcriber import Transcriber

        self.assertIsInstance(self._make("linux", "x86_64"), Transcriber)
        self.assertIsInstance(self._make("win32", "AMD64"), Transcriber)

    def test_mapeamento_de_repo(self):
        from scriba.stt_mlx import MlxWhisperProvider

        self.assertEqual(MlxWhisperProvider(mock.Mock(model="large-v3-turbo"))._repo(),
                         "mlx-community/whisper-large-v3-turbo")
        self.assertEqual(MlxWhisperProvider(mock.Mock(model="org/custom-repo"))._repo(),
                         "org/custom-repo")

    def test_segments_e_fallback_runtime(self):
        from scriba.stt_mlx import MlxWhisperProvider

        cfg = mock.Mock(model="tiny", language="pt", hotwords="SAP ABAP")
        fake_mlx = mock.Mock()
        fake_mlx.transcribe.return_value = {"segments": [
            {"start": 0.0, "end": 2.0, "text": " olá "},
            {"start": 2.0, "end": 3.0, "text": "  "},   # vazio: filtrado
        ]}
        prov = MlxWhisperProvider(cfg)
        with mock.patch.dict(sys.modules, {"mlx_whisper": fake_mlx}):
            segs = prov.transcribe("x.wav")
        self.assertEqual([(s.start, s.end, s.text) for s in segs], [(0.0, 2.0, "olá")])
        self.assertEqual(prov.device_used, "metal")
        # hotwords viraram initial_prompt
        self.assertEqual(fake_mlx.transcribe.call_args[1]["initial_prompt"], "SAP ABAP")

        # falha em runtime → fallback faster-whisper CPU (espelho do CUDA→CPU)
        fake_mlx.transcribe.side_effect = RuntimeError("metal explodiu")
        prov2 = MlxWhisperProvider(cfg)
        fake_fw = mock.Mock()
        fake_fw.ensure_loaded.return_value = "cpu"
        fake_fw.transcribe.return_value = ["seg"]
        with mock.patch.dict(sys.modules, {"mlx_whisper": fake_mlx}), \
                mock.patch("scriba.stt_mlx.Transcriber", return_value=fake_fw):
            self.assertEqual(prov2.transcribe("x.wav"), ["seg"])
        self.assertEqual(prov2.device_used, "cpu")


class TestSoNome(unittest.TestCase):
    def test_por_plataforma(self):
        with mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(util.so_nome(), "Windows")
        with _darwin():
            self.assertEqual(util.so_nome(), "macOS")
        with _linux():
            self.assertEqual(util.so_nome(), "Linux")


if __name__ == "__main__":
    unittest.main()
