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
SCHEMA_VERSION = 3  # v3 (#76): pendências (action items) viram snapshot no índice

# Separa o resumo da transcrição completa no notas.md. AMBOS entram no FTS (v2/#11):
# o resumo na coluna `body`, a transcrição na coluna `transcript` — assim a busca do
# dashboard acha em qualquer lugar (como a varredura antiga fazia). build_notes sempre
# escreve este cabeçalho no notas.md antes da transcrição.
_TRANSCRIPT_MARKER = "## Transcrição completa"

# Tombstone de exclusão pela UI (#16): uma pasta com este arquivo foi "excluída" pelo
# usuário MANTENDO o áudio como backup. O índice a ignora (não a ressuscita no reindex);
# (re)indexar explicitamente — ex.: re-summarize via build_notes → index_meeting —
# remove o tombstone e traz a nota de volta.
_DELETED_MARKER = ".deleted"

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
    # Pendências (#76): snapshot DERIVADO da seção "## Pendências e Ações" de cada nota,
    # cruzado com o `.actions.json` (fonte da verdade do estado). `state` já nasce string
    # p/ acomodar os estados futuros do épico (open/done/dismissed/archived) sem novo bump;
    # aqui só `open` e `done` são populados (o .actions.json de hoje só distingue feito).
    # `position` = ordem do item na nota (p/ listar na mesma sequência).
    """CREATE TABLE IF NOT EXISTS action_items (
        meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        key        TEXT NOT NULL,
        label      TEXT,
        text       TEXT,
        state      TEXT NOT NULL DEFAULT 'open',
        position   INTEGER
    )""",
    "CREATE INDEX IF NOT EXISTS ix_action_meeting ON action_items(meeting_id)",
    "CREATE INDEX IF NOT EXISTS ix_action_state   ON action_items(state)",
    # FTS5 standalone (guarda o próprio texto: simples e robusto p/ delete/rebuild).
    # rowid == meetings.id, mantido à mão no insert/delete. `body` = resumo,
    # `transcript` = transcrição completa (v2/#11) — MATCH sem coluna busca em todas.
    "CREATE VIRTUAL TABLE IF NOT EXISTS meetings_fts USING fts5(title, client, participants, body, transcript)",
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
        for tbl in ("meetings_fts", "action_items", "participants", "meetings"):
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


def _transcript_text(md: str) -> str:
    """Texto da transcrição completa (tudo DEPOIS do marcador) p/ a busca full-text."""
    if not md:
        return ""
    idx = md.find(_TRANSCRIPT_MARKER)
    return md[idx + len(_TRANSCRIPT_MARKER):].strip() if idx != -1 else ""


def _extract(folder: Path) -> dict | None:
    """Lê `meta.json` (+ `notas.md`) de uma pasta e monta o registro a indexar.
    None se não há `meta.json` (não é pasta de reunião) ou se a nota foi excluída pela
    UI (#16) mantendo o áudio — o tombstone `.deleted` impede o reindex de ressuscitá-la."""
    folder = Path(folder)
    if (folder / _DELETED_MARKER).exists():
        return None
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
    # Pendências (#76): itens da nota + estado do sidecar. `state` derivado do
    # `.actions.json` (fonte da verdade), já normalizado p/ estado nomeado (#77):
    # open/done/dismissed/archived; ausente = 'open'.
    action_state = notes.load_action_state(folder) if md else {}
    action_items = [
        {"key": it["key"], "label": it["label"], "text": it["text"],
         "state": action_state.get(it["key"], "open")}
        for it in (notes.parse_action_items(md) if md else [])
    ]
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
        "action_items": action_items,
        "body": _summary_body(md),
        "transcript": _transcript_text(md),
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
    action_items = rec.get("action_items") or []
    if action_items:
        conn.executemany(
            "INSERT INTO action_items (meeting_id, key, label, text, state, position)"
            " VALUES (?,?,?,?,?,?)",
            [(mid, a["key"], a["label"], a["text"], a["state"], i)
             for i, a in enumerate(action_items)],
        )
    names = " ".join(p["name"] for p in rec["participants"])
    conn.execute(
        "INSERT INTO meetings_fts (rowid, title, client, participants, body, transcript)"
        " VALUES (?,?,?,?,?,?)",
        (mid, rec["title"], rec["client"], names, rec["body"], rec.get("transcript", "")),
    )
    return mid


# -- API pública: indexar / remover / reindexar -----------------------------

def index_meeting(folder) -> bool:
    """(Re)indexa UMA reunião. Idempotente. NUNCA levanta — falha de índice não pode
    quebrar o pipeline de gravação; só loga e devolve False.

    (Re)indexar explicitamente DESFAZ uma exclusão pela UI (#16): remove o tombstone
    `.deleted` antes de extrair, então um re-summarize (build_notes) traz a nota de volta."""
    try:
        folder = Path(folder)
        try:
            (folder / _DELETED_MARKER).unlink(missing_ok=True)
        except OSError:
            pass
        rec = _extract(folder)
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


def set_action_state(folder, key: str, state: str) -> bool:
    """Atualiza no índice o `state` de UMA pendência (snapshot), sem reindexar a nota.
    Chamado por `notes.set_action_done` logo após gravar o `.actions.json` (que segue
    a FONTE DA VERDADE do estado), p/ capa/hub refletirem o clique na hora. NUNCA levanta
    (só loga e devolve False): o índice é derivado, um erro aqui não pode quebrar a UI —
    o próximo reindex reconstrói o snapshot a partir do `.actions.json` de qualquer forma.
    Devolve True se a linha existia e foi atualizada."""
    try:
        conn = _connect()
        try:
            with conn:
                cur = conn.execute(
                    "UPDATE action_items SET state = ? WHERE key = ? AND meeting_id = "
                    "(SELECT id FROM meetings WHERE folder = ?)",
                    (state, key, str(folder)),
                )
            return cur.rowcount > 0
        finally:
            conn.close()
    except Exception:
        log.exception("falha ao atualizar estado da pendência %s em %s", key, folder)
        return False


def mark_deleted(folder) -> None:
    """Exclusão pela UI (#16) MANTENDO o áudio: tira a reunião do índice agora E deixa um
    tombstone `.deleted` na pasta para o reindex não trazê-la de volta. O áudio + meta
    seguem como backup (recuperável via re-summarize, que limpa o tombstone ao reindexar).
    Para exclusão TOTAL (apagando a pasta), use `remove_meeting` + rmtree da pasta."""
    folder = Path(folder)
    remove_meeting(folder)
    if folder.is_dir():
        try:
            (folder / _DELETED_MARKER).write_text("", encoding="utf-8")
        except OSError:
            log.warning("não consegui marcar %s como excluída (tombstone)", folder)


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
            conn.execute("DELETE FROM action_items")
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


def reindex_if_needed(recordings_dir=None) -> int:
    """Reconstrói o índice SÓ se ele estiver vazio — index.db ausente, recriado por
    schema divergente (`_ensure_schema` dropa+recria) ou nunca indexado. No-op quando
    já há reuniões, então no boot do app (#12) o custo é zero depois da 1ª carga.
    Devolve o nº de reuniões no índice. (Drift de base já populada fica para o
    `scribadev reindex` manual / hooks incrementais.)"""
    n = count()  # conecta → _ensure_schema trata divergência de versão (pode zerar)
    if n == 0:
        return reindex(recordings_dir)
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


_FTS_NO_TRANSCRIPT = "{title client participants body}"  # colunas exceto `transcript`


def search(query=None, participant=None, client=None, since=None, until=None,
           status=None, limit=50, include_transcript=True) -> list[dict]:
    """Busca reuniões no índice. Filtros opcionais combinam (AND):

    - `query`       — full-text (FTS5) em título/cliente/participantes/resumo;
    - `participant` — nome (casa parcial, case-insensitive) entre os participantes;
    - `client`      — cliente (casa parcial);
    - `since`/`until` — intervalo por `started_at` (ISO 'YYYY-MM-DD' ou completo);
    - `status`      — status exato (ex.: 'done');
    - `include_transcript` — quando False, a busca de texto IGNORA a transcrição
      (só resumo/título/cliente/participantes). True = tudo (default).

    Devolve dicts (com a lista `participants`) ordenados por `started_at` desc.
    """
    conn = _connect()
    try:
        where, params, joins = [], [], ""
        if query and str(query).strip():
            # FTS5 exige o NOME da tabela no MATCH (alias vira "no such column")
            joins += " JOIN meetings_fts ON meetings_fts.rowid = m.id"
            where.append("meetings_fts MATCH ?")
            fq = _fts_query(query)
            if not include_transcript:
                # filtro de coluna do FTS5: restringe os termos às colunas != transcript
                fq = f"{_FTS_NO_TRANSCRIPT} : ({fq})"
            params.append(fq)
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


# -- pendências (action items): consulta do snapshot (#76) ------------------

def _action_filters(state=None, states=None, since=None, until=None, client=None,
                    folder=None) -> tuple[list[str], list]:
    """Cláusulas WHERE + params comuns a `action_counts` e `list_action_items`.
    `a` = action_items, `m` = meetings (o JOIN é montado por quem chama)."""
    where, params = [], []
    st = list(states) if states else ([state] if state else [])
    if st:
        where.append("a.state IN (%s)" % ",".join("?" * len(st)))
        params += [str(s) for s in st]
    if since:
        where.append("m.started_at >= ?")
        params.append(str(since))
    if until:
        where.append("m.started_at <= ?")
        params.append(_until_bound(until))
    if client:
        where.append("m.client LIKE ?")
        params.append(f"%{client}%")
    if folder:
        where.append("m.folder = ?")
        params.append(str(folder))
    return where, params


def action_counts(since=None, until=None, client=None) -> dict:
    """Contagem de pendências POR ESTADO (ex.: `{'open': 5, 'done': 3}`), aplicando o
    recorte temporal (`since`/`until` em `meetings.started_at`) e por `client`. Base do
    badge "N ativas" da capa (A4) e dos totais do hub (A3) — barato, sem re-parsear `.md`.
    Estados ausentes simplesmente não aparecem no dict (use `.get(estado, 0)`)."""
    conn = _connect()
    try:
        where, params = _action_filters(since=since, until=until, client=client)
        sql = ("SELECT a.state AS state, COUNT(*) AS n FROM action_items a "
               "JOIN meetings m ON m.id = a.meeting_id")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY a.state"
        return {r["state"]: r["n"] for r in conn.execute(sql, params).fetchall()}
    finally:
        conn.close()


def list_action_items(state=None, states=None, since=None, until=None, client=None,
                      folder=None, limit=500) -> list[dict]:
    """Pendências do índice já com o contexto da reunião (p/ o hub/capa abrirem a nota
    certa sem ler disco). Substitui o papel de `notes.open_action_items` (que re-parseava
    cada `.md`). Filtros combinam (AND): `state` (um) ou `states` (vários), recorte por
    `since`/`until` em `started_at`, `client` (parcial), `folder` (exato).

    Cada item volta com `key`/`label`/`text`/`state`/`position` + o contexto da reunião
    (`meeting_id`, `folder`, `export_path`, `title`, `meeting_title`, `client`,
    `started_at`). Ordenado por reunião mais recente primeiro, itens na ordem da nota."""
    conn = _connect()
    try:
        where, params = _action_filters(state=state, states=states, since=since,
                                        until=until, client=client, folder=folder)
        sql = ("SELECT a.key, a.label, a.text, a.state, a.position, "
               "m.id AS meeting_id, m.folder, m.export_path, m.title, "
               "m.meeting_title, m.client, m.started_at "
               "FROM action_items a JOIN meetings m ON m.id = a.meeting_id")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY m.started_at DESC, a.position ASC LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
