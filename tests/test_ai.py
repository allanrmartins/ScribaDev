"""Testes de scriba.ai: camada de IA configurável (claude CLI / Ollama / OpenAI-compat).

Sem rede real: monkeypatcha urllib.request.urlopen e subprocess.run.
"""

import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scriba import ai, config, util  # noqa: E402
from scriba.config import Summary  # noqa: E402


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


class HttpProviderTests(unittest.TestCase):
    """Ollama + OpenAI-compat: URL, payload, auth, e falha → None."""

    def setUp(self):
        self._real_urlopen = urllib.request.urlopen
        self.captured = {}

    def tearDown(self):
        urllib.request.urlopen = self._real_urlopen

    def _patch(self, response):
        def fake(req, timeout=None):
            self.captured["req"] = req
            self.captured["timeout"] = timeout
            if isinstance(response, Exception):
                raise response
            return _FakeResp(response)
        urllib.request.urlopen = fake

    # ---- Ollama ----
    def test_ollama_url_payload(self):
        self._patch({"message": {"content": "  ok  "}})
        cfg = Summary(provider="ollama", ollama_model="llama3.1", base_url="")
        out = ai._ollama(cfg, "sou o sistema", "sou o user", timeout=5)
        self.assertEqual(out, "ok")
        req = self.captured["req"]
        self.assertEqual(req.full_url, "http://localhost:11434/api/chat")
        self.assertEqual(req.method, "POST")
        body = json.loads(req.data)
        self.assertEqual(body["model"], "llama3.1")
        self.assertFalse(body["stream"])
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertEqual(body["messages"][1]["content"], "sou o user")

    def test_ollama_base_url_strip_barra(self):
        self._patch({"message": {"content": "ok"}})
        cfg = Summary(provider="ollama", ollama_model="m", base_url="http://host:1234/")
        ai._ollama(cfg, "s", "u", timeout=5)
        self.assertEqual(self.captured["req"].full_url, "http://host:1234/api/chat")

    def test_ollama_falha_de_conexao_vira_none(self):
        self._patch(urllib.error.URLError("recusada"))
        cfg = Summary(provider="ollama", ollama_model="m")
        self.assertIsNone(ai._ollama(cfg, "s", "u", timeout=5))

    # ---- OpenAI-compatível ----
    def test_openai_url_auth_payload(self):
        self._patch({"choices": [{"message": {"content": "ok"}}]})
        cfg = Summary(provider="openai", openai_model="gpt-4o-mini",
                      base_url="https://api.exemplo/v1/", api_key="sk-123")
        out = ai._openai(cfg, "s", "u", timeout=5)
        self.assertEqual(out, "ok")
        req = self.captured["req"]
        self.assertEqual(req.full_url, "https://api.exemplo/v1/chat/completions")
        self.assertEqual(req.get_header("Authorization"), "Bearer sk-123")
        self.assertEqual(json.loads(req.data)["model"], "gpt-4o-mini")

    def test_openai_campos_por_provider_tem_precedencia(self):  # #22
        self._patch({"choices": [{"message": {"content": "ok"}}]})
        cfg = Summary(provider="openai", openai_model="m",
                      openai_base_url="https://novo/v1", openai_api_key="sk-novo",
                      base_url="https://legado/v1", api_key="sk-legado")
        ai._openai(cfg, "s", "u", timeout=5)
        req = self.captured["req"]
        self.assertEqual(req.full_url, "https://novo/v1/chat/completions")
        self.assertEqual(req.get_header("Authorization"), "Bearer sk-novo")

    def test_openai_cai_no_legado_quando_novos_vazios(self):  # #22 retrocompat
        self._patch({"choices": [{"message": {"content": "ok"}}]})
        cfg = Summary(provider="openai", openai_model="m", base_url="https://legado/v1", api_key="sk-legado")
        ai._openai(cfg, "s", "u", timeout=5)
        req = self.captured["req"]
        self.assertEqual(req.full_url, "https://legado/v1/chat/completions")
        self.assertEqual(req.get_header("Authorization"), "Bearer sk-legado")

    def test_ollama_usa_ollama_base_url_com_precedencia(self):  # #22
        self._patch({"message": {"content": "ok"}})
        cfg = Summary(provider="ollama", ollama_model="m",
                      ollama_base_url="http://host:9/", base_url="http://legado:1")
        ai._ollama(cfg, "s", "u", timeout=5)
        self.assertEqual(self.captured["req"].full_url, "http://host:9/api/chat")

    def test_openai_sem_base_url_nao_faz_request(self):
        self._patch({"choices": [{"message": {"content": "nao deveria"}}]})
        cfg = Summary(provider="openai", base_url="", api_key="sk-1")
        self.assertIsNone(ai._openai(cfg, "s", "u", timeout=5))
        self.assertNotIn("req", self.captured)  # nem chegou a chamar urlopen

    def test_openai_resposta_malformada_vira_none(self):
        self._patch({"sem": "choices"})
        cfg = Summary(provider="openai", base_url="https://x/v1", api_key="k")
        self.assertIsNone(ai._openai(cfg, "s", "u", timeout=5))


class ClaudeCliTests(unittest.TestCase):
    def setUp(self):
        self._real_run = subprocess.run
        self._real_cmd = util.claude_command
        self.captured = {}
        util.claude_command = lambda: ["claude"]

    def tearDown(self):
        subprocess.run = self._real_run
        util.claude_command = self._real_cmd

    def _patch_run(self, rc=0, stdout="ok"):
        def fake(cmd, **kw):
            self.captured["cmd"] = cmd
            self.captured["kw"] = kw
            return type("P", (), {"returncode": rc, "stdout": stdout, "stderr": ""})()
        subprocess.run = fake

    def test_hidden_window_liga_creationflags(self):
        self._patch_run()
        cfg = Summary(provider="claude", model="claude-sonnet-4-6")
        out = ai._claude_cli(cfg, "sys", "payload", timeout=5, cwd=None, hidden_window=True)
        self.assertEqual(out, "ok")
        self.assertEqual(self.captured["kw"]["creationflags"], ai._CREATE_NO_WINDOW)
        self.assertEqual(self.captured["kw"]["input"], "payload")

    def test_sem_hidden_window_creationflags_zero(self):
        self._patch_run()
        cfg = Summary(provider="claude", model="m")
        ai._claude_cli(cfg, "sys", "p", timeout=5, cwd=None, hidden_window=False)
        self.assertEqual(self.captured["kw"]["creationflags"], 0)

    def test_rc_nao_zero_vira_none(self):
        self._patch_run(rc=1, stdout="")
        cfg = Summary(provider="claude", model="m")
        self.assertIsNone(ai._claude_cli(cfg, "s", "p", timeout=5, cwd=None, hidden_window=False))

    def test_cmd_ausente_vira_none(self):
        util.claude_command = lambda: None
        cfg = Summary(provider="claude", model="m")
        self.assertIsNone(ai._claude_cli(cfg, "s", "p", timeout=5, cwd=None, hidden_window=False))


class DispatchTests(unittest.TestCase):
    """complete() roteia pelo provider de [summary].provider."""

    def setUp(self):
        self._real_load = config.load
        self._calls = []
        for name in ("_claude_cli", "_ollama", "_openai"):
            setattr(self, "_real_" + name, getattr(ai, name))

    def tearDown(self):
        config.load = self._real_load
        for name in ("_claude_cli", "_ollama", "_openai"):
            setattr(ai, name, getattr(self, "_real_" + name))

    def _route(self, provider):
        import types as _t
        config.load = lambda: _t.SimpleNamespace(summary=Summary(provider=provider))
        ai._claude_cli = lambda *a, **k: "CLAUDE"
        ai._ollama = lambda *a, **k: "OLLAMA"
        ai._openai = lambda *a, **k: "OPENAI"
        return ai.complete("s", "u", timeout=5)

    def test_roteia_ollama(self):
        self.assertEqual(self._route("ollama"), "OLLAMA")

    def test_roteia_openai(self):
        self.assertEqual(self._route("openai"), "OPENAI")

    def test_roteia_claude_default(self):
        self.assertEqual(self._route("claude"), "CLAUDE")

    def test_provider_desconhecido_cai_no_claude(self):
        self.assertEqual(self._route("inexistente"), "CLAUDE")

    def test_model_override_aplica_ao_provider_ativo(self):  # #22 (modelo do chat)
        import types as _t
        cap = {}
        config.load = lambda: _t.SimpleNamespace(summary=Summary(provider="claude", model="claude-sonnet-4-6"))
        ai._claude_cli = lambda cfg, *a, **k: cap.setdefault("model", cfg.model)
        ai.complete("s", "u", timeout=5, model="claude-haiku-4-5")
        self.assertEqual(cap["model"], "claude-haiku-4-5")

    def test_sem_override_usa_o_modelo_do_resumo(self):
        import types as _t
        cap = {}
        config.load = lambda: _t.SimpleNamespace(summary=Summary(provider="claude", model="claude-sonnet-4-6"))
        ai._claude_cli = lambda cfg, *a, **k: cap.setdefault("model", cfg.model)
        ai.complete("s", "u", timeout=5)
        self.assertEqual(cap["model"], "claude-sonnet-4-6")


class ConfigRoundTripTests(unittest.TestCase):
    """Os 5 campos novos persistem; config antigo carrega como provider=claude."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="scriba_ai_"))
        util.APP_DIR = self.d
        util.LOGS_DIR = self.d / "logs"
        util.CONFIG_PATH = self.d / "config.toml"

    def test_defaults_no_default_config(self):
        cfg = config.load()  # cria DEFAULT_CONFIG
        self.assertEqual(cfg.summary.provider, "claude")
        self.assertEqual(cfg.summary.ollama_model, "llama3.1")
        self.assertEqual(cfg.summary.openai_model, "gpt-4o-mini")

    def test_round_trip_dos_campos_novos(self):
        import dataclasses
        cfg = config.load()
        novo = dataclasses.replace(
            cfg,
            summary=dataclasses.replace(
                cfg.summary, provider="openai", openai_base_url="https://api.x/v1",
                openai_api_key="sk-secreta", openai_model="qwen2.5",
            ),
        )
        config.save(novo)
        again = config.load()
        self.assertEqual(again.summary.provider, "openai")
        self.assertEqual(again.summary.openai_base_url, "https://api.x/v1")
        self.assertEqual(again.summary.openai_api_key, "sk-secreta")
        self.assertEqual(again.summary.openai_model, "qwen2.5")

    def test_config_antigo_sem_provider_assume_claude(self):
        # config sem a chave 'provider' (formato anterior) -> default claude
        util.CONFIG_PATH.write_text("[summary]\nenabled = true\nmodel = 'claude-opus-4-8'\n", encoding="utf-8")
        cfg = config.load()
        self.assertEqual(cfg.summary.provider, "claude")
        self.assertEqual(cfg.summary.model, "claude-opus-4-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
