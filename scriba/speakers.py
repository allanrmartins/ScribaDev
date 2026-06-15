"""Enrollment de voz incremental (issue #1): reconhece participantes recorrentes.

A diarização (pyannote) separa as vozes em "Participante 1/2/…" anônimos e entrega
um EMBEDDING (vetor 256-d do wespeaker) por voz. Aqui guardamos (embedding → nome)
num store local e, em reuniões futuras, comparamos cada voz nova com as cadastradas
(similaridade de cosseno); acima do limiar, a voz recebe o nome conhecido. Tudo
100% local — sem tocar no Teams/Meet/Zoom.

Store: %LOCALAPPDATA%/ScribaDev/speakers.json — uma entrada por pessoa, com o
centroide acumulado dos embeddings já vistos (enrollment incremental: melhora a
cada rotulagem). O vetor fica como lista de floats (JSON legível; são poucas
pessoas × 256 dims). "Eu" (trilha do mic) é sempre o usuário e NÃO entra aqui.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from . import util

log = logging.getLogger("scriba.speakers")

# Limiar de cosseno para considerar duas vozes a mesma pessoa. Conservador de
# propósito: rotular com o nome ERRADO é pior que deixar "Participante N". Os
# embeddings do wespeaker separam pessoas distintas bem abaixo disto.
DEFAULT_THRESHOLD = 0.55

STORE_PATH = util.APP_DIR / "speakers.json"


def _cosine(a, b) -> float:
    import numpy as np

    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def load_store() -> list[dict]:
    """Lista de pessoas cadastradas: [{name, embedding:[float], n:int, updated}]."""
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def save_store(entries: list[dict]) -> None:
    util.ensure_app_dirs()
    util.atomic_write_text(STORE_PATH, json.dumps(entries, ensure_ascii=False, indent=2))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def match(embedding, entries: list[dict] | None = None, threshold: float = DEFAULT_THRESHOLD) -> tuple[str | None, float]:
    """Melhor pessoa cadastrada para `embedding`: (nome, score) se score ≥ limiar,
    senão (None, melhor_score). `entries` reaproveita um store já lido (evita reler
    o disco a cada voz)."""
    if entries is None:
        entries = load_store()
    best_name, best_score = None, -1.0
    for e in entries:
        emb = e.get("embedding")
        if not emb:
            continue
        s = _cosine(embedding, emb)
        if s > best_score:
            best_name, best_score = e.get("name"), s
    if best_name is not None and best_score >= threshold:
        return best_name, best_score
    return None, max(best_score, 0.0)


def enroll(name: str, embedding) -> None:
    """Aprende (ou reforça) a voz de `name`. Incremental: se a pessoa já existe,
    funde o novo embedding ao centroide acumulado (média ponderada por nº de
    amostras) — assim o reconhecimento melhora a cada rotulagem."""
    import numpy as np

    name = (name or "").strip()
    if not name:
        return
    emb = np.asarray(embedding, dtype=np.float32)
    if emb.size == 0:
        return
    entries = load_store()
    for e in entries:
        if str(e.get("name", "")).strip().lower() == name.lower():
            n = int(e.get("n", 1) or 1)
            old = np.asarray(e.get("embedding") or emb, dtype=np.float32)
            if old.shape == emb.shape:
                emb = (old * n + emb) / (n + 1)
            e["embedding"] = emb.tolist()
            e["n"] = n + 1
            e["name"] = name  # normaliza a grafia para a última informada
            e["updated"] = _now()
            save_store(entries)
            log.info("voz reforçada: %s (n=%d)", name, n + 1)
            return
    entries.append({"name": name, "embedding": emb.tolist(), "n": 1, "updated": _now()})
    save_store(entries)
    log.info("voz cadastrada: %s", name)
