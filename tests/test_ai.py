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

    def _patch_run(self, rc=0, stdout="ok", stderr=""):
        def fake(cmd, **kw):
            self.captured["cmd"] = cmd
            self.captured["kw"] = kw
            return type("P", (), {"returncode": rc, "stdout": stdout, "stderr": stderr})()
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

    def test_nao_logado_marca_last_error(self):
        # a CLI escreve "Not logged in" no STDOUT (não stderr) e sai 1: precisa ser
        # detectado p/ a nota avisar o /login em vez do genérico "rode summarize".
        ai.last_error = None
        self._patch_run(rc=1, stdout="Not logged in · Please run /login", stderr="")
        cfg = Summary(provider="claude", model="m")
        self.assertIsNone(ai._claude_cli(cfg, "s", "p", timeout=5, cwd=None, hidden_window=False))
        self.assertEqual(ai.last_error, ai.ERR_LOGGED_OUT)

    def test_falha_generica_nao_marca_logged_out(self):
        ai.last_error = None
        self._patch_run(rc=1, stdout="", stderr="boom qualquer erro")
        cfg = Summary(provider="claude", model="m")
        self.assertIsNone(ai._claude_cli(cfg, "s", "p", timeout=5, cwd=None, hidden_window=False))
        self.assertIsNone(ai.last_error)

    def test_oauth_expirado_marca_logged_out(self):
        # 2026-10-05: a sessão venceu com outro texto, e a nota saiu com a dica genérica
        ai.last_error = None
        self._patch_run(rc=1, stdout="Failed to authenticate: OAuth session expired and "
                                     "could not be refreshed")
        cfg = Summary(provider="claude", model="m")
        self.assertIsNone(ai._claude_cli(cfg, "s", "p", timeout=5, cwd=None, hidden_window=False))
        self.assertEqual(ai.last_error, ai.ERR_LOGGED_OUT)


class _Relogio:
    """Substitui ai.time: sleep avança o relógio em vez de dormir."""

    def __init__(self):
        self.agora = 0.0
        self.sonos = 0

    def monotonic(self):
        return self.agora

    def sleep(self, s):
        self.sonos += 1
        self.agora += s


class ClaudeCliEsperaTests(unittest.TestCase):
    """O auto-update do Claude Code reinstala o pacote npm e o `claude` some do PATH
    por alguns segundos: o resumo que caía nessa janela era pulado em silêncio
    (2026-10-08). Com cli_wait, o resumo espera a CLI voltar."""

    def setUp(self):
        self._real_run = subprocess.run
        self._real_cmd = util.claude_command
        self._real_time = ai.time
        self.relogio = _Relogio()
        ai.time = self.relogio
        ai.last_error = None
        self.runs = []

    def tearDown(self):
        subprocess.run = self._real_run
        util.claude_command = self._real_cmd
        ai.time = self._real_time
        ai.last_error = None

    def _cli_volta_apos(self, polls):
        """claude_command() devolve None nas `polls` primeiras chamadas."""
        estado = {"n": 0}

        def cmd():
            estado["n"] += 1
            return None if estado["n"] <= polls else ["claude"]
        util.claude_command = cmd

    def _runs(self, *respostas):
        fila = list(respostas)

        def fake(cmd, **kw):
            self.runs.append(cmd)
            rc, out = fila.pop(0)
            return type("P", (), {"returncode": rc, "stdout": out, "stderr": ""})()
        subprocess.run = fake

    def _cli(self, **kw):
        cfg = Summary(provider="claude", model="m")
        return ai._claude_cli(cfg, "s", "p", timeout=5, cwd=None, hidden_window=False, **kw)

    def test_cli_volta_durante_a_espera(self):
        self._cli_volta_apos(3)
        self._runs((0, "resumo"))
        self.assertEqual(self._cli(cli_wait=180), "resumo")
        self.assertEqual(len(self.runs), 1)
        self.assertIsNone(ai.last_error)

    def test_cli_nao_volta_desiste_no_prazo(self):
        util.claude_command = lambda: None
        self._runs()
        self.assertIsNone(self._cli(cli_wait=30))
        self.assertEqual(ai.last_error, ai.ERR_NOT_FOUND)
        self.assertLessEqual(self.relogio.agora, 30 + ai._CLI_POLL_S)
        self.assertEqual(self.runs, [])

    def test_sem_cli_wait_nao_espera(self):
        # chat e wizard têm usuário esperando: falham na hora, como sempre
        util.claude_command = lambda: None
        self.assertIsNone(self._cli())
        self.assertEqual(self.relogio.sonos, 0)
        self.assertEqual(ai.last_error, ai.ERR_NOT_FOUND)

    def test_pacote_some_sob_o_shim_tenta_de_novo(self):
        util.claude_command = lambda: ["claude"]
        self._runs((1, "Error: Cannot find module 'C:\\npm\\node_modules\\cli.js'"), (0, "resumo"))
        self.assertEqual(self._cli(cli_wait=180), "resumo")
        self.assertEqual(len(self.runs), 2)

    def test_shim_some_entre_achar_e_rodar_tenta_de_novo(self):
        # o cmd.exe reclama em texto localizado: o sinal é o shim fora do PATH
        estado = {"n": 0}

        def cmd():
            estado["n"] += 1
            return None if estado["n"] in (2, 3) else ["claude"]
        util.claude_command = cmd
        self._runs((1, "'claude.cmd' não é reconhecido como um comando interno"), (0, "resumo"))
        self.assertEqual(self._cli(cli_wait=180), "resumo")
        self.assertEqual(len(self.runs), 2)

    def test_falha_comum_nao_repete(self):
        util.claude_command = lambda: ["claude"]
        self._runs((1, "boom"))
        self.assertIsNone(self._cli(cli_wait=180))
        self.assertEqual(len(self.runs), 1)


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
