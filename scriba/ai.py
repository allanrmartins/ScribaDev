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
import os
import subprocess
import urllib.error
import urllib.request

from . import config, util

_OLLAMA_DEFAULT = "http://localhost:11434"
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Motivo da última falha de provider, para o chamador dar uma dica PRECISA na nota
# (ver notes.build_notes) em vez do genérico "rode scriba summarize" — que falharia
# igual se a causa for a CLI deslogada. Reposto no início de generate_summary; escrito
# no caminho de falha. Sequencial no worker de resumo; um chat concorrente no máximo
# troca o texto da dica (cosmético), nunca quebra o resumo.
ERR_LOGGED_OUT = "logged_out"
last_error: str | None = None


def complete(
    system_prompt: str,
    user_payload: str,
    *,
    timeout: int,
    cwd=None,
    hidden_window: bool = False,
    model: str | None = None,
) -> str | None:
    """Roda o provider de IA configurado sobre (system_prompt, user_payload).

    Retorna o texto da resposta, ou None em qualquer falha. `cwd`/`hidden_window`
    só valem para o provider claude (CLI) — são ignorados nos providers HTTP.
    `model` sobrescreve o modelo do provider ATIVO (ex.: o chat usa um modelo mais
    barato que o resumo); None = usa o modelo configurado para o resumo.
    """
    import dataclasses

    cfg = config.load().summary
    provider = (cfg.provider or "claude").strip().lower()
    if model:
        if provider == "ollama":
            cfg = dataclasses.replace(cfg, ollama_model=model)
        elif provider == "openai":
            cfg = dataclasses.replace(cfg, openai_model=model)
        else:
            cfg = dataclasses.replace(cfg, model=model)
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
    # Desliga o raciocínio estendido (MAX_THINKING_TOKENS=0): para RESUMIR (extrair +
    # organizar, não raciocinar) o thinking é desperdício — benchmark mediu ~14,6k
    # tokens de thinking (80% da geração) por ~3x o tempo e 2x o custo, com qualidade
    # IGUAL ou melhor sem ele. Vale para o resumo e o test_connection (únicos usos).
    env = {**os.environ, "MAX_THINKING_TOKENS": "0"}
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
            env=env,
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
        # A CLI escreve alguns erros (ex.: "Not logged in · Please run /login") no STDOUT,
        # não no stderr — olhe os DOIS, senão o diagnóstico sai mudo ("retornou 1" pelado,
        # que foi o que aconteceu quando a sessão expirou).
        combined = f"{proc.stderr or ''}\n{proc.stdout or ''}"
        if _looks_logged_out(combined):
            global last_error
            last_error = ERR_LOGGED_OUT
            print(f"resumo: claude CLI não está logada - rode `claude` e faça /login "
                  f"(retornou {proc.returncode})")
        else:
            tail = combined.strip().splitlines()
            print(f"resumo: claude retornou {proc.returncode}" + (f" ({tail[-1]})" if tail else ""))
        return None
    return proc.stdout.strip()


def _looks_logged_out(text: str) -> bool:
    """A CLI claude imprime "Not logged in · Please run /login" e sai 1 quando a sessão
    expirou. Detecta isso para o chamador avisar o login em vez do genérico de re-rodar."""
    t = (text or "").lower()
    return "not logged in" in t or "/login" in t


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
    base = (cfg.ollama_base_url or cfg.base_url or _OLLAMA_DEFAULT).rstrip("/")
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
    base = (cfg.openai_base_url or cfg.base_url or "").rstrip("/")
    if not base:
        print("IA: base_url do provider OpenAI-compatível vazio — configure o endpoint (inclua /v1)")
        return None
    key = cfg.openai_api_key or cfg.api_key
    headers = {"Authorization": f"Bearer {key}"} if key else {}
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
        base = cfg.ollama_base_url or cfg.base_url or _OLLAMA_DEFAULT
        return (bool(out), f"Ollama respondeu ({base})" if out else "sem resposta do Ollama — ele está rodando? modelo baixado?")
    if provider == "openai":
        if not (cfg.openai_base_url or cfg.base_url):
            return (False, "base_url vazio — informe o endpoint (inclua /v1)")
        out = _openai(cfg, sp, payload, timeout=t)
        return (bool(out), "endpoint respondeu" if out else "sem resposta — confira URL, chave e modelo")
    # claude
    if util.claude_command() is None:
        return (False, "claude CLI não encontrado")
    out = _claude_cli(cfg, sp, payload, timeout=t, cwd=None, hidden_window=True)
    return (bool(out), "claude CLI respondeu" if out else "claude CLI não respondeu")
