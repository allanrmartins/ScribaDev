"""Retenção: apaga gravações já transcritas com sucesso após N dias.

Só remove pastas em `recordings_dir` cujo `meta.json` está com `status="done"`
(transcrição + nota concluídas) e mais velhas que `[audio].retention_days`.
A nota final (.md), que vive na pasta de notas (`output.export_dir`), é separada
e **nunca** é tocada aqui. `retention_days = 0` desliga a limpeza.

Duas camadas fecham a janela TOCTOU entre find_purgeable e o rmtree:
  1. Re-leitura (guarda complementar): reler o meta.json imediatamente antes do
     rmtree e pular se o status mudou para em-andamento. Cobre o caso comum.
  2. Arquivo .lock (guarda real): o worker (pipeline.process_folder) cria o
     arquivo .lock ao iniciar e remove em finally; a retenção NÃO remove pastas
     com .lock presente, mesmo que o status ainda pareça "done". Esta segunda
     camada fecha a janela mesmo na corrida mais apertada (status muda no
     milissegundo entre reler e rmtree).
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from . import util

log = logging.getLogger("scriba.retention")


def _age_days(meta: dict, folder: Path) -> float | None:
    """Idade da gravação em dias (ended_at/started_at do meta; mtime da pasta de reserva)."""
    stamp = meta.get("ended_at") or meta.get("started_at")
    dt = None
    if stamp:
        try:
            dt = datetime.fromisoformat(stamp)
        except ValueError:
            dt = None
    if dt is None:
        try:
            dt = datetime.fromtimestamp(folder.stat().st_mtime)
        except OSError:
            return None
    return (datetime.now() - dt).total_seconds() / 86400.0


def find_purgeable(cfg) -> list[tuple[Path, float]]:
    """Pastas elegíveis: status=done e idade >= retention_days. [(pasta, idade_dias)].

    retention_days <= 0 desliga (lista vazia). Só considera subpastas diretas de
    recordings_dir com meta.json legível — defensivo contra apagar algo inesperado.
    """
    days = int(getattr(cfg.audio, "retention_days", 0) or 0)
    if days <= 0:
        return []
    rec_dir = cfg.output.resolved_recordings_dir()
    out: list[tuple[Path, float]] = []
    # rglob: cobre a árvore ano/mês/dia e as pastas legadas (planas)
    for meta_path in sorted(rec_dir.rglob("meta.json")):
        folder = meta_path.parent
        if folder == rec_dir:
            continue  # meta.json solto na raiz não é uma gravação
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("status") != "done":
            continue  # só gravações transcritas com sucesso
        age = _age_days(meta, folder)
        if age is not None and age >= days:
            out.append((folder, age))
    return out


def _prune_empty_dirs(rec_dir: Path, start: Path) -> None:
    """Sobe removendo pastas de data (dia/mês/ano) que ficaram vazias."""
    d = start
    while d != rec_dir and d.is_dir():
        try:
            next(d.iterdir())
            return  # ainda tem conteúdo
        except StopIteration:
            pass
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent


def purge_old_recordings(cfg, dry_run: bool = False) -> list[tuple[Path, float]]:
    """Remove (ou só lista, se dry_run) as gravações elegíveis. Retorna as afetadas.

    Antes de cada rmtree, duas checagens fecham a janela TOCTOU (ver docstring do módulo):
    1. .lock: presença de worker ativo — guarda real, fecha a corrida mais apertada.
    2. re-leitura do meta.json: status pode ter mudado desde find_purgeable — guarda
       complementar para o caso em que o .lock ainda não existe mas o status já mudou.
    """
    from . import util as util_mod

    rec_dir = cfg.output.resolved_recordings_dir()
    affected: list[tuple[Path, float]] = []
    for folder, age in find_purgeable(cfg):
        if dry_run:
            affected.append((folder, age))
            continue

        # --- guarda real: .lock criado pelo worker em process_folder ---
        if util.is_locked(folder):
            log.info("retenção: pulando %s — .lock ativo (worker em andamento)", folder.name)
            continue

        # --- guarda complementar: reler status antes do rmtree ---
        # cobre o caso em que o .lock ainda não existia mas o status já mudou
        try:
            meta_now = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            status_now = meta_now.get("status")
        except Exception:
            log.debug("retenção: pulando %s — meta.json ilegível na re-leitura", folder.name)
            continue
        if status_now != "done":
            log.info(
                "retenção: pulando %s — status mudou para '%s' desde find_purgeable",
                folder.name, status_now,
            )
            continue

        try:
            shutil.rmtree(folder)
        except Exception:
            log.exception("retenção: falha ao remover %s", folder)
            continue
        _prune_empty_dirs(rec_dir, folder.parent)
        log.info("retenção: removida %s (%.0f dias)", folder.name, age)
        affected.append((folder, age))
    return affected
