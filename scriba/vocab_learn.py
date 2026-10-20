"""Vocabulário que aprende com as reuniões (#227).

O resumo (Claude) devolve, junto com título e cliente, os TERMOS técnicos da reunião
na grafia correta. Cada termo vira candidato; quando aparece em `MIN_REUNIOES`
reuniões diferentes, vira SUGESTÃO. Nada entra sozinho: o usuário aceita (✓) ou
recusa (✗) nas Configurações - um termo errado no vocabulário puxa o Whisper para a
palavra errada e faz a correção (vocab_fix) trocar palavra certa por ele.

Os aceitos ficam AQUI, não no texto do vocabulário que o usuário digitou: o formato
livre dele (palavras soltas, vírgulas, linhas) continua intocado, e o uso soma as duas
listas (`all_terms` p/ correção e resumo, `whisper_hotwords` p/ o prompt do Whisper).

Arquivo: APP_DIR/vocab_learned.json (derivado e descartável - apagar só esquece o
aprendizado). Quem escreve: o subprocesso do pipeline (registro) e a GUI (aceitar/
recusar); cada escrita relê o arquivo antes, e a escrita é atômica.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from . import util, vocab_fix

log = logging.getLogger("scriba.vocab_learn")

MIN_REUNIOES = 2
PENDENTE, ACEITO, RECUSADO = "pendente", "aceito", "recusado"
_lock = threading.Lock()


def _path() -> Path:
    return util.APP_DIR / "vocab_learned.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data.get("terms"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "terms": {}}


def _save(data: dict) -> None:
    util.atomic_write_text(_path(), json.dumps(data, ensure_ascii=False, indent=1))


def parse_terms_line(line: str) -> list[str]:
    """`TERMOS: SE80, ZPL, SmartForms` -> ["SE80", "ZPL", "SmartForms"] (sem vazios,
    sem duplicados; "-"/"nenhum" = lista vazia)."""
    out: list[str] = []
    for t in (line or "").replace(";", ",").split(","):
        t = t.strip().strip("`\"'").strip()
        if t and t.lower() not in ("-", "nenhum", "nenhuma", "n/a", "?") and t not in out:
            out.append(t)
    return out


def _parece_identificador(termo: str) -> bool:
    """Filtro local do que o Claude devolver: identificador técnico (sigla, código,
    nome próprio de ferramenta), não palavra comum nem frase. 1 ou 2 palavras, a 1ª
    começando por maiúscula ou dígito, ou com dígito em algum lugar ("base64")."""
    palavras = termo.split()
    if not 1 <= len(palavras) <= 2:
        return False
    return termo[0].isupper() or termo[0].isdigit() or any(c.isdigit() for c in termo)


def record(meeting: str, terms: list[str], hotwords: str) -> list[str]:
    """Registra os termos de UMA reunião. Devolve os que viraram sugestão AGORA
    (acabaram de atingir MIN_REUNIOES) - p/ o aviso ao usuário. Nunca levanta."""
    try:
        conhecidos = {vocab_fix._norm(t) for t in vocab_fix.parse_terms(hotwords)}
        agora = datetime.now().isoformat(timespec="seconds")
        novos: list[str] = []
        with _lock:
            data = _load()
            for termo in terms:
                chave = vocab_fix._norm(termo)
                if len(chave) < 2 or chave in conhecidos or not _parece_identificador(termo):
                    continue
                item = data["terms"].setdefault(
                    chave, {"term": termo, "meetings": [], "first": agora, "status": PENDENTE})
                item["term"] = termo            # grafia mais recente que o Claude escreveu
                item["last"] = agora
                if meeting not in item["meetings"]:
                    item["meetings"].append(meeting)
                    if item["status"] == PENDENTE and len(item["meetings"]) == MIN_REUNIOES:
                        novos.append(termo)
            _save(data)
        return novos
    except Exception:
        log.exception("vocabulário: falha ao registrar termos (seguindo)")
        return []


def suggestions() -> list[dict]:
    """Sugestões a revisar: pendentes em MIN_REUNIOES+ reuniões, mais frequentes antes."""
    itens = [dict(v, key=k) for k, v in _load()["terms"].items()
             if v.get("status") == PENDENTE and len(v.get("meetings", [])) >= MIN_REUNIOES]
    return sorted(itens, key=lambda v: (-len(v["meetings"]), v["term"].casefold()))


def accepted() -> list[str]:
    return sorted((v["term"] for v in _load()["terms"].values() if v.get("status") == ACEITO),
                  key=str.casefold)


def set_status(term: str, status: str) -> None:
    """Aceita/recusa (ou devolve a pendente) um termo pela grafia exibida."""
    with _lock:
        data = _load()
        item = data["terms"].get(vocab_fix._norm(term))
        if item is not None:
            item["status"] = status
            _save(data)


def all_terms(hotwords: str) -> list[str]:
    """Vocabulário digitado + aprendidos aceitos (correção do texto e resumo)."""
    termos = vocab_fix.parse_terms(hotwords)
    vistos = {vocab_fix._norm(t) for t in termos}
    return termos + [t for t in accepted() if vocab_fix._norm(t) not in vistos]


def whisper_hotwords(hotwords: str) -> str:
    """Texto p/ o prompt do Whisper: o digitado + os aceitos (soltos por espaço)."""
    extra = [t for t in accepted() if vocab_fix._norm(t) not in
             {vocab_fix._norm(x) for x in vocab_fix.parse_terms(hotwords)}]
    return " ".join([hotwords.strip(), *extra]).strip()
