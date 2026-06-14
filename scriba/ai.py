"""Camada de IA configurável: resumo da reunião + geração de prompt do wizard.

Um único ponto de despacho — `complete()` — lê `[summary].provider` do config e
roteia para o **claude CLI** (legado), o **Ollama** (HTTP local, sem chave) ou um
endpoint **OpenAI-compatível** (HTTP, com chave BYO do usuário). O HTTP usa só a
stdlib (`urllib`): sem dependência nova, para não pesar no empacotamento (.exe).

Contrato: toda falha retorna `None` (o chamador trata como "pula o resumo") e
imprime uma linha de diagnóstico — mesmo estilo do código atual.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request

from . import config, util

_OLLAMA_DEFAULT = "http://localhost:11434"
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def complete(
    system_prompt: str,
    user_payload: str,
    *,
    timeout: int,
    cwd=None,
    hidden_window: bool = False,
) -> str | None:
    """Roda o provider de IA configurado sobre (system_prompt, user_payload).

    Retorna o texto da resposta, ou None em qualquer falha. `cwd`/`hidden_window`
    só valem para o provider claude (CLI) — são ignorados nos providers HTTP.
    """
    cfg = config.load().summary
    provider = (cfg.provider or "claude").strip().lower()
    if provider == "ollama":
        return _ollama(cfg, system_prompt, user_payload, timeout=timeout)
    if provider == "openai":
        return _openai(cfg, system_prompt, user_payload, timeout=timeout)
    if provider != "claude":
        print(f"IA: provider '{provider}' desconhecido — usando o claude CLI")
    return _claude_cli(cfg, system_prompt, user_payload, timeout=timeout, cwd=cwd, hidden_window=hidden_window)


# ----------------------------------------------------------- provider: claude --

def _claude_cli(cfg, system_prompt, user_payload, *, timeout, cwd, hidden_window):
    cmd = util.claude_command()
    if cmd is None:
        print("claude CLI não encontrado — pulando resumo")
        return None
    flags = [
        "-p",
        # 1 linha no argv: o shim .cmd do npm + cmd.exe truncam args com quebras
        "--system-prompt", " ".join(system_prompt.split()),
        "--tools", "",
        "--no-session-persistence",
        "--model", cfg.model,
        "--output-format", "text",
    ]
    try:
        proc = subprocess.run(
            cmd + flags,
            input=user_payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd) if cwd else str(util.APP_DIR),
            timeout=timeout,
            # hidden_window=True só no caminho da bandeja (pythonw sem console); no
            # worker de processamento fica False de propósito — forçar a flag lá
            # dispara o bug do Windows Terminal (janela visível).
            creationflags=_CREATE_NO_WINDOW if hidden_window else 0,
        )
    except subprocess.TimeoutExpired:
        print(f"resumo: timeout após {timeout}s")
        return None
    except Exception as e:
        print(f"resumo: erro ao executar claude ({e})")
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        err = (proc.stderr or "").strip().splitlines()
        print(f"resumo: claude retornou {proc.returncode}" + (f" ({err[-1]})" if err else ""))
        return None
    return proc.stdout.strip()


# ------------------------------------------------------------- providers HTTP --

def _http_json(url: str, body: dict, *, timeout: int, headers: dict | None = None) -> dict | None:
    """POST JSON e devolve o JSON da resposta, ou None (com diagnóstico) em falha."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = " " + e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        print(f"IA: HTTP {e.code} em {url}{detail}")
        return None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        print(f"IA: falha de conexão em {url} ({e})")
        return None


def _ollama(cfg, system_prompt, user_payload, *, timeout):
    base = (cfg.base_url or _OLLAMA_DEFAULT).rstrip("/")
    body = {
        "model": cfg.ollama_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        "stream": False,
    }
    data = _http_json(f"{base}/api/chat", body, timeout=timeout)
    if not data:
        return None
    try:
        text = data["message"]["content"].strip()
    except (KeyError, TypeError, AttributeError):
        print(f"IA: resposta inesperada do Ollama: {str(data)[:200]}")
        return None
    return text or None


def _openai(cfg, system_prompt, user_payload, *, timeout):
    base = (cfg.base_url or "").rstrip("/")
    if not base:
        print("IA: base_url do provider OpenAI-compatível vazio — configure o endpoint (inclua /v1)")
        return None
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    body = {
        "model": cfg.openai_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        "stream": False,
    }
    data = _http_json(f"{base}/chat/completions", body, timeout=timeout, headers=headers)
    if not data:
        return None
    try:
        text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        print(f"IA: resposta inesperada do endpoint OpenAI-compatível: {str(data)[:200]}")
        return None
    return text or None


# -------------------------------------------------------------- teste rápido --

def test_connection(cfg=None) -> tuple[bool, str]:
    """Round-trip mínimo p/ validar o provider configurado. Retorna (ok, mensagem).

    Timeout curto de propósito: serve ao botão 'Testar conexão' e ao `scriba diag`,
    nunca deve travar a UI pelos `timeout_seconds` do resumo.
    """
    cfg = cfg or config.load().summary
    provider = (cfg.provider or "claude").strip().lower()
    sp = "Você responde com uma única palavra, sem pontuação."
    payload = "Responda exatamente: ok"
    t = 20
    if provider == "ollama":
        out = _ollama(cfg, sp, payload, timeout=t)
        base = cfg.base_url or _OLLAMA_DEFAULT
        return (bool(out), f"Ollama respondeu ({base})" if out else "sem resposta do Ollama — ele está rodando? modelo baixado?")
    if provider == "openai":
        if not cfg.base_url:
            return (False, "base_url vazio — informe o endpoint (inclua /v1)")
        out = _openai(cfg, sp, payload, timeout=t)
        return (bool(out), "endpoint respondeu" if out else "sem resposta — confira URL, chave e modelo")
    # claude
    if util.claude_command() is None:
        return (False, "claude CLI não encontrado")
    out = _claude_cli(cfg, sp, payload, timeout=t, cwd=None, hidden_window=True)
    return (bool(out), "claude CLI respondeu" if out else "claude CLI não respondeu")
