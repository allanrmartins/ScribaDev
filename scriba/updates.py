"""Checagem e aplicação de atualizações do ScribaDev.

- CHECAGEM (qualquer usuário, sem token): lê um MANIFESTO público de versão (um Gist
  com {"version","url","notes"}). Funciona mesmo com o repositório-fonte PRIVADO — não
  embute segredo no cliente (regra do roadmap). Allan publica a versão "liberada" lá;
  bumpar = editar o Gist.
- APLICAÇÃO (só instalação via git/editável): `git pull --ff-only` na pasta-fonte +
  reinstala (pip -e) se as dependências mudaram. Instalação sem git → abre o link do
  manifesto (download/instruções).

Tudo defensivo: rede/git nunca levanta para o chamador (UI/CLI). Versão local: fonte
única em scriba.__version__.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import __version__

log = logging.getLogger("scriba.updates")

REPO = "allanrmartins/ScribaDev"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
# Manifesto PÚBLICO de versão (Gist sob a conta pessoal). Token-free; cobre git e ZIP.
# Bumpar a versão liberada = editar este Gist (campo "version").
MANIFEST_URL = (
    "https://gist.githubusercontent.com/allanrmartins/"
    "90154257cbf89ebc146cfbecdac53701/raw/latest.json"
)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# -- versões (puro) ---------------------------------------------------------

def _parse_ver(s) -> tuple[int, int, int] | None:
    """'v1.2.3' / '1.2.3' -> (1, 2, 3); None se não casar X.Y.Z."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(s or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def _is_newer(current, latest) -> bool:
    """True se `latest` for estritamente maior que `current` (semver X.Y.Z, comparação
    NUMÉRICA — '0.10.0' > '0.9.0'). Qualquer valor inválido/ausente → False."""
    c, l = _parse_ver(current), _parse_ver(latest)
    return bool(c and l and l > c)


# -- string de versão p/ exibir (semver + build do git) ---------------------

_BUILD_CACHE = None


def _describe_to_str(d: str, base: str) -> str:
    """(puro) Formata a saída de `git describe` num rótulo a partir de `base` ('vX.Y.Z'):
    exato na tag → base; N commits depois → 'base · +N · hash'; com mod. local → sufixo."""
    d = (d or "").strip()
    if not d:
        return base
    dirty = " (modificado)" if d.endswith("-dirty") else ""
    if dirty:
        d = d[: -len("-dirty")]
    m = re.search(r"-(\d+)-g([0-9a-fA-F]+)$", d)
    if m:
        return f"{base} · +{m.group(1)} · {m.group(2)}{dirty}"
    if re.fullmatch(r"v?\d+\.\d+\.\d+", d):  # exatamente numa tag de release
        return f"{base}{dirty}"
    return f"{base} · {d}{dirty}"  # só hash (sem tag) etc.


def build_string() -> str:
    """Versão p/ exibir: 'v0.3.0 · +3 · 9cba847' (instalação git — sobe a cada commit)
    ou 'v0.3.0' (na tag / sem git). Cacheado — não muda durante a execução."""
    global _BUILD_CACHE
    if _BUILD_CACHE is not None:
        return _BUILD_CACHE
    base = f"v{__version__}"
    d = ""
    root = repo_root()
    if root is not None:
        try:
            r = _git(root, "describe", "--tags", "--always", "--dirty", timeout=10)
            if r.returncode == 0:
                d = r.stdout.strip()
        except Exception:
            d = ""
    _BUILD_CACHE = _describe_to_str(d, base)
    return _BUILD_CACHE


# -- checagem (manifesto público) -------------------------------------------

def _http_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={
        "User-Agent": f"ScribaDev/{__version__}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def latest_info(timeout: float = 4.0) -> dict | None:
    """Manifesto público de versão: {'version': 'X.Y.Z', 'url': '<download>', 'notes'}.
    None se indisponível (sem rede) ou malformado. Não levanta. O `?cb=` fura o cache de
    CDN do raw do Gist (que servia a versão antiga por minutos), então o aviso é imediato."""
    import time

    try:
        data = _http_json(f"{MANIFEST_URL}?cb={int(time.time())}", timeout)
        if isinstance(data, dict) and _parse_ver(data.get("version")):
            return data
    except Exception as e:
        log.debug("manifesto de versão indisponível: %s", e)
    return None


def latest_version(timeout: float = 4.0) -> str | None:
    """Última versão publicada (do manifesto) como 'X.Y.Z', ou None se indisponível."""
    info = latest_info(timeout)
    return info.get("version") if info else None


def update_available(timeout: float = 4.0) -> str | None:
    """Versão nova (str) se o manifesto tiver algo MAIOR que a local; senão None."""
    latest = latest_version(timeout)
    return latest if _is_newer(__version__, latest) else None


# Campos OPCIONAIS do manifesto com o instalador por SO (#144/épico #138). Instalações
# git antigas só leem "version"/"url" e seguem funcionando (retrocompatível); quando o
# campo do SO existe, o "Baixar" cai DIRETO no arquivo certo em vez da página de releases.
_INSTALLER_KEYS = {"win32": "installer_win", "darwin": "installer_mac"}


def download_url() -> str:
    """Link de download p/ ESTA instalação: o instalador do SO quando o manifesto o
    publica (installer_win/installer_mac), senão o `url` genérico do manifesto,
    senão a página de releases."""
    info = latest_info() or {}
    key = _INSTALLER_KEYS.get(sys.platform)
    return (info.get(key) if key else None) or info.get("url") or RELEASES_PAGE


def is_frozen_install() -> bool:
    """True rodando do bundle congelado do PyInstaller (instalador #142) — não há
    fonte nem venv: atualizar = baixar e rodar o instalador novo (que preserva os
    dados do usuário). A UI/CLI usam isto p/ diagnóstico e mensagens."""
    return bool(getattr(sys, "frozen", False))


# -- aplicação (instalação via git) -----------------------------------------

def repo_root() -> Path | None:
    """Raiz do repositório-fonte se for instalação via git (o pacote roda direto do
    clone, via editable install); senão None."""
    root = Path(__file__).resolve().parent.parent
    return root if (root / ".git").is_dir() else None


def is_git_install() -> bool:
    return repo_root() is not None


def disk_version(root: Path) -> str | None:
    """Lê o __version__ do scriba/__init__.py EM DISCO (após o pull o processo em
    memória ainda tem a versão velha)."""
    try:
        txt = (root / "scriba" / "__init__.py").read_text(encoding="utf-8")
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)', txt)
        return m.group(1) if m else None
    except OSError:
        return None


def _git(root: Path, *args: str, timeout: float = 120):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, timeout=timeout, creationflags=_NO_WINDOW)


def _pip_interpreter(executable: str, exists=Path.exists) -> str:
    """(puro/testável) Interpretador que deve rodar o `pip install` a partir de
    `executable`. NUNCA devolve o pythonw.exe.

    Por quê: ao (re)instalar, o setuptools/distlib regera o launcher dos gui-scripts
    (scribadev-tray.exe) derivando o interpretador do exe atual com um replace ingênuo
    'python' -> 'pythonw'. A partir de 'pythonw.exe' isso vira 'pythonww.exe' (com dois
    'w'), que não existe — e o app deixa de abrir pelo atalho/bandeja com
    "Unable to create process using ...\\pythonww.exe". Como a bandeja roda sob o
    pythonw.exe (sem console), sys.executable É o pythonw.exe e cairíamos exatamente
    nesse caso. Rodar o pip pelo python.exe irmão gera o launcher correto (-> pythonw.exe).
    Se o irmão não existir, mantém o original (não inventa caminho)."""
    py = Path(executable)
    if py.name.lower() == "pythonw.exe":
        sibling = py.with_name("python.exe")
        if exists(sibling):
            return str(sibling)
    return executable


def apply_git_update() -> tuple[bool, str]:
    """Atualiza uma instalação via git: `git pull --ff-only` + reinstala (pip -e) se o
    pyproject mudou. Retorna (ok, mensagem). Sem git → (False, instrução p/ baixar)."""
    root = repo_root()
    if root is None:
        if is_frozen_install():
            return (False, "Instalação por instalador: baixe e rode o instalador novo "
                    "(seus dados e notas são preservados):\n" + download_url())
        return (False, "Instalação sem git (ZIP/binário). Baixe a nova versão em:\n" + download_url())
    try:
        before = _git(root, "rev-parse", "HEAD").stdout.strip()
        pull = _git(root, "pull", "--ff-only")
        if pull.returncode != 0:
            return (False, "git pull falhou:\n" + (pull.stderr or pull.stdout).strip())
        after = _git(root, "rev-parse", "HEAD").stdout.strip()
        if before == after:
            return (True, "Você já está na versão mais recente.")
        changed = _git(root, "diff", "--name-only", before, after).stdout
        if "pyproject.toml" in changed:  # deps podem ter mudado → reinstala o editable
            # pelo python.exe (nunca pythonw.exe) p/ não gerar o launcher quebrado
            # scribadev-tray.exe -> pythonww.exe. Ver _pip_interpreter.
            pip = subprocess.run([_pip_interpreter(sys.executable), "-m", "pip", "install", "-e", str(root)],
                                 capture_output=True, text=True, timeout=600, creationflags=_NO_WINDOW)
            if pip.returncode != 0:
                return (False, "Código atualizado, mas a reinstalação de dependências falhou:\n"
                        + (pip.stderr or pip.stdout).strip())
        v = disk_version(root) or "?"
        return (True, f"Atualizado para a v{v}. Reinicie o ScribaDev para aplicar.")
    except FileNotFoundError:
        return (False, "git não encontrado no PATH. Instale o Git para atualizar por aqui.")
    except subprocess.TimeoutExpired:
        return (False, "Tempo esgotado ao atualizar (git/pip demorou demais).")
    except Exception as e:
        log.exception("falha ao aplicar atualização")
        return (False, f"Falha ao atualizar: {e}")
