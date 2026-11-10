"""Retenção: apaga gravações já transcritas com sucesso após N dias.

Só remove pastas em `recordings_dir` cujo `meta.json` está com `status="done"`
(transcrição + nota concluídas) e mais velhas que `[audio].retention_days`.
A nota final (.md), que vive na pasta de notas (`output.export_dir`), é separada
e **nunca** é tocada aqui. `retention_days = 0` desliga a limpeza.

Prints da call (#229) têm regra própria: a limpeza da gravação apaga tudo da
pasta MENOS `prints/`, que fica para consulta. Eles só saem pela
`purge_old_prints`, com `[audio].prints_retention_days` (0 = nunca, o padrão).

Gravações de teste (bandeja > "Gravar teste") somem inteiras 24 h depois, com a
nota exportada e os prints: `purge_test_meetings`.

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
            kept = remove_keeping_prints(folder)
        except Exception:
            log.exception("retenção: falha ao remover %s", folder)
            continue
        if not kept:
            _prune_empty_dirs(rec_dir, folder.parent)
        log.info("retenção: removida %s (%.0f dias)%s", folder.name, age,
                 " - prints mantidos" if kept else "")
        # remove do índice de busca (#10): a pasta (fonte da verdade) já foi apagada
        try:
            from . import meetings_index

            meetings_index.remove_meeting(folder)
        except Exception:
            pass
        affected.append((folder, age))
    return affected


# ------------------------------------------------------------- prints (#229) --

def remove_keeping_prints(folder: Path) -> bool:
    """Apaga a pasta da gravação, menos a subpasta `prints/` (se tiver algum print).
    Sem o meta.json, a pasta deixa de ser uma reunião para o índice e para esta
    limpeza; os prints seguem só a regra deles. Devolve True se manteve prints."""
    from .prints import PRINTS_DIR

    pdir = folder / PRINTS_DIR
    if not (pdir.is_dir() and any(p.is_file() for p in pdir.iterdir())):
        shutil.rmtree(folder)
        return False
    for child in folder.iterdir():
        if child.name == PRINTS_DIR:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    return True


def _old_files(folder: Path, days: int, now: float) -> list[Path]:
    out = []
    try:
        for f in folder.iterdir():
            try:
                if f.is_file() and (now - f.stat().st_mtime) / 86400.0 >= days:
                    out.append(f)
            except OSError:
                continue
    except OSError:
        pass
    return out


def purge_old_prints(cfg, dry_run: bool = False, now: float | None = None) -> list[Path]:
    """Apaga os prints da call com mais de `[audio].prints_retention_days` dias (0 =
    nunca). Vale para `prints/` das gravações e para a cópia ao lado das notas
    exportadas (`<data>_prints/`). A idade é a do próprio arquivo (o horário do print).
    Pasta que fica vazia sai junto. Pasta com processamento em andamento (.lock) é
    pulada. Devolve os arquivos removidos (ou que seriam, com dry_run)."""
    import time

    from .prints import PRINTS_DIR

    days = int(getattr(cfg.audio, "prints_retention_days", 0) or 0)
    if days <= 0:
        return []
    now = time.time() if now is None else now
    rec_dir = cfg.output.resolved_recordings_dir()
    dirs: list[tuple[Path, Path | None]] = []  # (pasta de prints, raiz p/ podar vazias)
    for pdir in sorted(rec_dir.rglob(PRINTS_DIR)):
        if pdir.is_dir() and not util.is_locked(pdir.parent):
            dirs.append((pdir, rec_dir))
    try:
        export_dir = cfg.output.resolved_export_dir()
        dirs += [(d, None) for d in sorted(export_dir.glob("*_prints")) if d.is_dir()]
    except Exception:
        pass
    removed: list[Path] = []
    for pdir, root in dirs:
        for f in _old_files(pdir, days, now):
            if not dry_run:
                try:
                    f.unlink()
                except OSError:
                    log.warning("retenção de prints: não consegui apagar %s", f)
                    continue
            removed.append(f)
        if dry_run:
            continue
        try:
            pdir.rmdir()  # só sai se ficou vazia
        except OSError:
            continue
        if root is not None:
            _prune_empty_dirs(root, pdir.parent)
    if removed and not dry_run:
        log.info("retenção de prints: %d print(s) > %d dias removido(s)", len(removed), days)
    return removed


# --------------------------------------------------------- gravações de teste --

TEST_TTL_HOURS = 24
# terminais: a gravação já virou nota (ou falhou de vez); as outras ainda andam
_TEST_FINAL_STATUSES = ("done", "failed", "no_audio")


def purge_test_meetings(cfg, now: datetime | None = None) -> list[Path]:
    """Apaga as gravações de teste (`"test": true` no meta) com mais de 24 h: a pasta
    da gravação inteira (prints incluídos), a nota exportada, o .bak dela e a cópia
    dos prints ao lado, e a entrada no índice. Pula as que ainda processam (.lock ou
    status não terminal). Devolve as pastas removidas. Vale mesmo com a retenção
    normal desligada: teste nunca é trabalho a guardar."""
    from . import meetings_index, prints

    now = now or datetime.now()
    rec_dir = cfg.output.resolved_recordings_dir()
    removed: list[Path] = []
    for meta_path in sorted(rec_dir.rglob("meta.json")):
        folder = meta_path.parent
        if folder == rec_dir:
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not meta.get("test") or meta.get("status") not in _TEST_FINAL_STATUSES:
            continue
        stamp = meta.get("ended_at") or meta.get("started_at") or ""
        try:
            age_h = (now - datetime.fromisoformat(stamp)).total_seconds() / 3600.0
        except ValueError:
            continue
        if age_h < TEST_TTL_HOURS or util.is_locked(folder):
            continue
        note = Path(meta["export_path"]) if meta.get("export_path") else None
        if note is not None:
            for f in (note, note.with_suffix(".md.bak")):
                try:
                    f.unlink(missing_ok=True)
                except OSError as e:
                    log.warning("teste: não apaguei %s: %s", f.name, e)
            shutil.rmtree(note.parent / prints.export_dir_name(note), ignore_errors=True)
        meetings_index.remove_meeting(folder)
        try:
            shutil.rmtree(folder)
        except OSError:
            log.exception("teste: falha ao remover %s", folder)
            continue
        _prune_empty_dirs(rec_dir, folder.parent)
        log.info("teste: gravação removida após %d h: %s", TEST_TTL_HOURS, folder.name)
        removed.append(folder)
    return removed
