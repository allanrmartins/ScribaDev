"""Índice de busca das reuniões (#10): SQLite + FTS5, DERIVADO e RECONSTRUÍVEL.

As pastas de gravação (recordings_dir) continuam a FONTE DA VERDADE — este índice
é só um *cache* de busca sobre os `meta.json` + `notas.md`. Pode ser apagado a
qualquer momento e reconstruído com `reindex()` (CLI: `scribadev reindex`); por
isso não há migração de dados: schema novo = dropa e reindexa. Nada aqui guarda o
áudio nem a transcrição completa — só metadados + o texto do resumo p/ busca
full-text (FTS5). Decisões da #10.

DB: %LOCALAPPDATA%/ScribaDev/index.db  (sqlite3 da stdlib; zero dependência nova,
nenhum servidor, custo desprezível ao lado de Whisper/diarização/claude).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from . import util

log = logging.getLogger("scriba.meetings_index")

DB_PATH = util.APP_DIR / "index.db"
SCHEMA_VERSION = 1

# Separa o resumo (indexado p/ busca) da transcrição completa (NÃO indexada na v1:
# é o grosso do texto — ver "decisões em aberto" da #10). build_notes sempre escreve
# este cabeçalho no notas.md antes da transcrição.
_TRANSCRIPT_MARKER = "## Transcrição completa"

_DDL = (
    """CREATE TABLE IF NOT EXISTS meetings (
        id            INTEGER PRIMARY KEY,
        folder        TEXT UNIQUE NOT NULL,
        started_at    TEXT,
        ended_at      TEXT,
        duration_s    INTEGER,
        status        TEXT,
        title         TEXT,
        client        TEXT,
        meeting_title TEXT,
        export_path   TEXT,
        indexed_at    TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_meetings_started ON meetings(started_at)",
    "CREATE INDEX IF NOT EXISTS ix_meetings_client  ON meetings(client)",
    """CREATE TABLE IF NOT EXISTS participants (
        meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        name       TEXT NOT NULL,
        role       TEXT,
        kind       TEXT                 -- 'present' | 'mentioned'
    )""",
    "CREATE INDEX IF NOT EXISTS ix_part_name    ON participants(name)",
    "CREATE INDEX IF NOT EXISTS ix_part_meeting ON participants(meeting_id)",
    # FTS5 standalone (guarda o próprio texto: simples e robusto p/ delete/rebuild).
    # rowid == meetings.id, mantido à mão no insert/delete.
    "CREATE VIRTUAL TABLE IF NOT EXISTS meetings_fts USING fts5(title, client, participants, body)",
)


# -- conexão + schema -------------------------------------------------------

def _connect() -> sqlite3.Connection:
    util.ensure_app_dirs()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")  # 2 workers finalizando juntos: espera o lock
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version and version != SCHEMA_VERSION:
        # índice é reconstruível: schema divergente = dropa tudo e recria vazio
        # (reindexar repopula a partir das pastas — sem migração de dados dolorosa).
        log.warning("index.db schema v%s != v%s — recriando (reindexe p/ repopular)", version, SCHEMA_VERSION)
        for tbl in ("meetings_fts", "participants", "meetings"):
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        version = 0
    for ddl in _DDL:
        conn.execute(ddl)
    if version != SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# -- extração de uma pasta de gravação --------------------------------------

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _summary_body(md: str) -> str:
    """Texto do resumo (tudo ANTES da transcrição completa) p/ a busca full-text."""
    if not md:
        return ""
    idx = md.find(_TRANSCRIPT_MARKER)
    return (md[:idx] if idx != -1 else md).strip()


def _extract(folder: Path) -> dict | None:
    """Lê `meta.json` (+ `notas.md`) de uma pasta e monta o registro a indexar.
    None se não há `meta.json` (não é pasta de reunião)."""
    folder = Path(folder)
    meta = _read_json(folder / "meta.json")
    if not meta:
        return None
    md = ""
    notas = folder / "notas.md"
    if notas.exists():
        try:
            md = notas.read_text(encoding="utf-8")
        except OSError:
            md = ""
    from . import notes  # lazy: evita ciclo (notes importa este módulo no build_notes)

    presentes, mencionados = notes.parse_participants(md) if md else ({}, [])
    parts = [{"name": n, "role": (r or "").strip(), "kind": "present"} for n, r in presentes.items()]
    parts += [{"name": n, "role": "", "kind": "mentioned"} for n in mencionados]
    dur = meta.get("duration_seconds")
    return {
        "folder": str(folder),
        "started_at": meta.get("started_at") or "",
        "ended_at": meta.get("ended_at") or "",
        "duration_s": int(float(dur)) if dur else None,
        "status": meta.get("status") or "",
        "title": (meta.get("title") or "").strip(),
        "client": (meta.get("client") or "").strip(),
        "meeting_title": (meta.get("meeting_title") or "").strip(),
        "export_path": meta.get("export_path") or "",
        "participants": parts,
        "body": _summary_body(md),
    }


def _upsert(conn: sqlite3.Connection, rec: dict) -> int:
    """Insere/atualiza UMA reunião (apaga a entrada anterior da pasta e reinsere).
    Mantém o rowid do FTS alinhado ao meetings.id. Devolve o id."""
    row = conn.execute("SELECT id FROM meetings WHERE folder = ?", (rec["folder"],)).fetchone()
    if row is not None:
        old_id = row["id"]
        conn.execute("DELETE FROM meetings_fts WHERE rowid = ?", (old_id,))
        conn.execute("DELETE FROM meetings WHERE id = ?", (old_id,))  # cascata p/ participants
    cur = conn.execute(
        """INSERT INTO meetings
           (folder, started_at, ended_at, duration_s, status, title, client,
            meeting_title, export_path, indexed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (rec["folder"], rec["started_at"], rec["ended_at"], rec["duration_s"],
         rec["status"], rec["title"], rec["client"], rec["meeting_title"],
         rec["export_path"], _now()),
    )
    mid = cur.lastrowid
    if rec["participants"]:
        conn.executemany(
            "INSERT INTO participants (meeting_id, name, role, kind) VALUES (?,?,?,?)",
            [(mid, p["name"], p["role"], p["kind"]) for p in rec["participants"]],
        )
    names = " ".join(p["name"] for p in rec["participants"])
    conn.execute(
        "INSERT INTO meetings_fts (rowid, title, client, participants, body) VALUES (?,?,?,?,?)",
        (mid, rec["title"], rec["client"], names, rec["body"]),
    )
    return mid


# -- API pública: indexar / remover / reindexar -----------------------------

def index_meeting(folder) -> bool:
    """(Re)indexa UMA reunião. Idempotente. NUNCA levanta — falha de índice não pode
    quebrar o pipeline de gravação; só loga e devolve False."""
    try:
        rec = _extract(Path(folder))
        if rec is None:
            return False
        conn = _connect()
        try:
            with conn:
                _upsert(conn, rec)
        finally:
            conn.close()
        return True
    except Exception:
        log.exception("falha ao indexar %s", folder)
        return False


def remove_meeting(folder) -> bool:
    """Remove uma reunião do índice (ex.: retenção apagou a pasta). True se removeu."""
    try:
        conn = _connect()
        try:
            with conn:
                row = conn.execute("SELECT id FROM meetings WHERE folder = ?", (str(folder),)).fetchone()
                if row is None:
                    return False
                conn.execute("DELETE FROM meetings_fts WHERE rowid = ?", (row["id"],))
                conn.execute("DELETE FROM meetings WHERE id = ?", (row["id"],))
            return True
        finally:
            conn.close()
    except Exception:
        log.exception("falha ao remover %s do índice", folder)
        return False


def reindex(recordings_dir=None) -> int:
    """Reconstrói o índice do zero a partir das pastas de gravação. Devolve o nº de
    reuniões indexadas. Rede de segurança contra drift (pasta apagada à mão, schema
    novo) — barato porque o índice é descartável."""
    if recordings_dir is None:
        from .config import load

        recordings_dir = load().output.resolved_recordings_dir()
    recordings_dir = Path(recordings_dir)
    conn = _connect()
    n = 0
    try:
        with conn:
            conn.execute("DELETE FROM meetings_fts")
            conn.execute("DELETE FROM participants")
            conn.execute("DELETE FROM meetings")
        for meta_path in sorted(recordings_dir.rglob("meta.json")):
            rec = _extract(meta_path.parent)
            if rec is None:
                continue
            with conn:
                _upsert(conn, rec)
            n += 1
    finally:
        conn.close()
    log.info("reindex: %d reuniões indexadas de %s", n, recordings_dir)
    return n


# -- busca ------------------------------------------------------------------

def _fts_query(text: str) -> str:
    """Neutraliza a sintaxe do FTS5: cada termo vira uma string entre aspas (AND
    implícito). Evita erro de sintaxe com hífen/aspas/operadores que o usuário digite."""
    terms = [t for t in str(text).split() if t]
    return " ".join('"' + t.replace('"', '""') + '"' for t in terms)


def _until_bound(until: str) -> str:
    """Limite superior inclusivo: uma data 'YYYY-MM-DD' vira o fim daquele dia."""
    u = str(until)
    return u + "T23:59:59" if len(u) == 10 else u


def search(query=None, participant=None, client=None, since=None, until=None,
           status=None, limit=50) -> list[dict]:
    """Busca reuniões no índice. Filtros opcionais combinam (AND):

    - `query`       — full-text (FTS5) em título/cliente/participantes/resumo;
    - `participant` — nome (casa parcial, case-insensitive) entre os participantes;
    - `client`      — cliente (casa parcial);
    - `since`/`until` — intervalo por `started_at` (ISO 'YYYY-MM-DD' ou completo);
    - `status`      — status exato (ex.: 'done').

    Devolve dicts (com a lista `participants`) ordenados por `started_at` desc.
    """
    conn = _connect()
    try:
        where, params, joins = [], [], ""
        if query and str(query).strip():
            # FTS5 exige o NOME da tabela no MATCH (alias vira "no such column")
            joins += " JOIN meetings_fts ON meetings_fts.rowid = m.id"
            where.append("meetings_fts MATCH ?")
            params.append(_fts_query(query))
        if participant:
            where.append("m.id IN (SELECT meeting_id FROM participants WHERE name LIKE ?)")
            params.append(f"%{participant}%")
        if client:
            where.append("m.client LIKE ?")
            params.append(f"%{client}%")
        if since:
            where.append("m.started_at >= ?")
            params.append(str(since))
        if until:
            where.append("m.started_at <= ?")
            params.append(_until_bound(until))
        if status:
            where.append("m.status = ?")
            params.append(status)
        sql = "SELECT m.* FROM meetings m" + joins
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY m.started_at DESC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["participants"] = [
                dict(p) for p in conn.execute(
                    "SELECT name, role, kind FROM participants WHERE meeting_id = ? ORDER BY kind, name",
                    (r["id"],)).fetchall()
            ]
            out.append(d)
        return out
    finally:
        conn.close()


def count() -> int:
    """Nº de reuniões no índice (conveniência p/ diagnóstico)."""
    conn = _connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    finally:
        conn.close()
