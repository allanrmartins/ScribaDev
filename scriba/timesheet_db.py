"""Banco DURÁVEL do apontamento de horas (timesheet) — épico #118, fundação (#119).

Ao contrário do index.db (cache derivado e descartável — meetings_index.py), este
banco é FONTE DA VERDADE: apontamentos não se reconstroem de lugar nenhum. Daí as
regras deliberadamente OPOSTAS às do índice:
- migração de schema é INCREMENTAL por user_version — NUNCA dropa nada;
- versão futura (downgrade do app) aborta com erro claro em vez de tocar no arquivo;
- backup explícito via Connection.backup() com rotação — o banco vive fora do
  OneDrive (SQLite vivo em pasta sincronizada corrompe no meio do write); o
  backup, arquivo morto, pode ir para pasta sincronizada.

DORMÊNCIA (#126): importar este módulo não cria arquivo, pasta nem conexão. O
banco só nasce no primeiro connect() — que, com [timesheet].enabled = false,
nenhum ponto de integração do app chama.

Concorrência: app e CLI podem escrever ao mesmo tempo (WAL + busy_timeout);
o subprocesso de pipeline nunca toca aqui. Conexão por operação, sem cache.

DB: %LOCALAPPDATA%/ScribaDev/timesheet.db (sqlite3 da stdlib; zero dependência).
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from . import util

log = logging.getLogger("scriba.timesheet_db")

# Caminho do banco. None = resolvido dinamicamente de util.APP_DIR a cada conexão
# (mesmo racional do meetings_index.DB_PATH: isolar APP_DIR num teste já redireciona
# o banco). O boot do app aponta para cá o override de [timesheet].db_path.
DB_PATH = None
SCHEMA_VERSION = 1

_DDL_V1 = (
    """CREATE TABLE clients (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
        active     INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE client_aliases (
        id        INTEGER PRIMARY KEY,
        client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        alias     TEXT NOT NULL UNIQUE COLLATE NOCASE
    )""",
    "CREATE INDEX ix_alias_client ON client_aliases(client_id)",
    """CREATE TABLE projects (
        id        INTEGER PRIMARY KEY,
        client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        code      TEXT NOT NULL,
        label     TEXT NOT NULL DEFAULT '',
        active    INTEGER NOT NULL DEFAULT 1,
        UNIQUE (client_id, code)
    )""",
    # client_id/client_text: quando o alias não resolve o nome vindo da IA, a linha
    # nasce com client_id NULL e o texto cru preservado (a UI oferece cadastrar).
    # minutes desnormalizado: totais por dia/mês viram SUM simples (recalculado aqui
    # em todo insert/update). meeting_started_at: chave ESTÁVEL de dedupe — a pasta
    # é renomeada pós-processamento e re-summarize muda título/cliente; started_at não.
    """CREATE TABLE entries (
        id                 INTEGER PRIMARY KEY,
        work_date          TEXT NOT NULL,
        start_time         TEXT NOT NULL,
        end_time           TEXT NOT NULL,
        minutes            INTEGER NOT NULL,
        client_id          INTEGER REFERENCES clients(id) ON DELETE SET NULL,
        client_text        TEXT NOT NULL DEFAULT '',
        project_id         INTEGER REFERENCES projects(id) ON DELETE SET NULL,
        project_text       TEXT NOT NULL DEFAULT '',
        description        TEXT NOT NULL DEFAULT '',
        overtime           INTEGER NOT NULL DEFAULT 0,
        location           TEXT NOT NULL DEFAULT '',
        posted             INTEGER NOT NULL DEFAULT 0,
        posted_at          TEXT,
        status             TEXT NOT NULL DEFAULT 'confirmed',
        origin             TEXT NOT NULL DEFAULT 'manual',
        meeting_folder     TEXT,
        meeting_started_at TEXT,
        created_at         TEXT NOT NULL,
        updated_at         TEXT NOT NULL
    )""",
    "CREATE INDEX ix_entries_date   ON entries(work_date)",
    "CREATE INDEX ix_entries_status ON entries(status)",
    "CREATE INDEX ix_entries_posted ON entries(posted)",
    # 1 reunião = no máx. 1 apontamento, garantido no banco (à prova de corrida
    # app+CLI). Sugestão rejeitada vira status='discarded' (não DELETE) justamente
    # para manter este índice ocupado e a sugestão não ressuscitar no reprocesso.
    """CREATE UNIQUE INDEX ux_entries_meeting ON entries(meeting_started_at)
        WHERE meeting_started_at IS NOT NULL""",
    """CREATE TABLE day_notes (
        work_date TEXT PRIMARY KEY,
        note      TEXT NOT NULL
    )""",
)

# Migrações incrementais: {versão alvo: DDLs que levam da versão anterior até ela}.
_MIGRATIONS: dict[int, tuple[str, ...]] = {1: _DDL_V1}

_ENTRY_STATUSES = ("suggested", "confirmed", "discarded")


def _db_path() -> Path:
    return DB_PATH if DB_PATH is not None else util.APP_DIR / "timesheet.db"


# -- conexão + schema ---------------------------------------------------------

def connect() -> sqlite3.Connection:
    """Abre (criando na primeira vez) o banco do timesheet.

    É AQUI que o banco nasce — criação lazy, parte do contrato de dormência (#126):
    enquanto o módulo estiver desativado, nada chama connect() e nenhum arquivo
    aparece na máquina.
    """
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")  # app + CLI escrevendo juntos
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        _ensure_schema(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        # Downgrade do app sobre banco mais novo: dado primário, NUNCA dropar nem
        # adivinhar — aborta sem tocar no arquivo (oposto do meetings_index).
        raise RuntimeError(
            f"timesheet.db é de versão futura (v{version} > v{SCHEMA_VERSION}) — "
            "atualize o ScribaDev para usar o apontamento de horas"
        )
    for target in range(version + 1, SCHEMA_VERSION + 1):
        with conn:  # cada passo de migração é atômico
            for ddl in _MIGRATIONS[target]:
                conn.execute(ddl)
            conn.execute(f"PRAGMA user_version = {target}")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# -- validação ----------------------------------------------------------------

def _check_date(work_date: str) -> str:
    try:
        datetime.strptime(work_date or "", "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"data inválida (use AAAA-MM-DD): {work_date!r}") from None
    return work_date


def _minutes_between(start_time: str, end_time: str) -> int:
    """Duração start→end em minutos; exige HH:MM válidos e fim > início.

    Virada de meia-noite não é suportada aqui de propósito: o motor de sugestões
    clampa o fim em 23:59 antes de chegar ao banco, e entrada manual cruzando o
    dia são dois apontamentos.
    """
    for label, v in (("início", start_time), ("fim", end_time)):
        if not util.time_hhmm_ok(v or ""):
            raise ValueError(f"hora de {label} inválida (use HH:MM): {v!r}")
    t0 = datetime.strptime(start_time, "%H:%M")
    t1 = datetime.strptime(end_time, "%H:%M")
    mins = int((t1 - t0).total_seconds() // 60)
    if mins <= 0:
        raise ValueError(f"fim ({end_time}) deve ser depois do início ({start_time})")
    return mins


# -- apontamentos (entries) ---------------------------------------------------

def add_entry(*, work_date: str, start_time: str, end_time: str,
              client_id: int | None = None, client_text: str = "",
              project_id: int | None = None, project_text: str = "",
              description: str = "", overtime: bool = False, location: str = "",
              origin: str = "manual", status: str = "confirmed",
              meeting_folder: str | None = None,
              meeting_started_at: str | None = None) -> int:
    """Insere um apontamento e devolve o id. Valida data/horas; minutes é derivado."""
    _check_date(work_date)
    minutes = _minutes_between(start_time, end_time)
    if status not in _ENTRY_STATUSES:
        raise ValueError(f"status inválido: {status!r}")
    now = _now()
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            """INSERT INTO entries (work_date, start_time, end_time, minutes,
                   client_id, client_text, project_id, project_text, description,
                   overtime, location, status, origin, meeting_folder,
                   meeting_started_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (work_date, start_time, end_time, minutes, client_id, client_text,
             project_id, project_text, description, int(bool(overtime)), location,
             status, origin, meeting_folder, meeting_started_at, now, now),
        )
        return cur.lastrowid


# Campos que update_entry aceita; qualquer outro é erro de programação (fail fast).
_UPDATABLE = {
    "work_date", "start_time", "end_time", "client_id", "client_text",
    "project_id", "project_text", "description", "overtime", "location",
    "posted", "status",
}


def update_entry(entry_id: int, **fields) -> bool:
    """Atualiza campos de um apontamento; recalcula minutes se data/hora mudou.

    Devolve False se o id não existe. posted=True/False mantém posted_at coerente.
    """
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"campos não atualizáveis: {sorted(unknown)}")
    if "status" in fields and fields["status"] not in _ENTRY_STATUSES:
        raise ValueError(f"status inválido: {fields['status']!r}")
    if not fields:
        return False
    with closing(connect()) as conn, conn:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            return False
        merged = dict(row) | fields
        _check_date(merged["work_date"])
        fields["minutes"] = _minutes_between(merged["start_time"], merged["end_time"])
        if "overtime" in fields:
            fields["overtime"] = int(bool(fields["overtime"]))
        if "posted" in fields:
            fields["posted"] = int(bool(fields["posted"]))
            fields["posted_at"] = _now() if fields["posted"] else None
        fields["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE entries SET {sets} WHERE id = ?",
                     (*fields.values(), entry_id))
        return True


def delete_entry(entry_id: int) -> bool:
    """Apaga um apontamento MANUAL. Linha ligada a reunião não se apaga: use
    status='discarded' (o tombstone é o que impede a sugestão de ressuscitar)."""
    with closing(connect()) as conn, conn:
        row = conn.execute(
            "SELECT meeting_started_at FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return False
        if row["meeting_started_at"] is not None:
            raise ValueError(
                "apontamento vindo de reunião não se apaga — descarte a sugestão "
                "(status='discarded') para ela não voltar no reprocesso"
            )
        conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        return True


def list_entries(*, month: str | None = None, day: str | None = None,
                 status: str | None = None, posted: bool | None = None,
                 client_id: int | None = None) -> list[dict]:
    """Apontamentos filtrados, ordenados por dia e hora de início.

    month = 'AAAA-MM'; day = 'AAAA-MM-DD'. Traz client_name resolvido (JOIN) para
    lista/CLI não precisarem de segunda consulta.
    """
    where, args = [], []
    if month:
        where.append("e.work_date LIKE ?")
        args.append(f"{month}-%")
    if day:
        where.append("e.work_date = ?")
        args.append(day)
    if status:
        where.append("e.status = ?")
        args.append(status)
    if posted is not None:
        where.append("e.posted = ?")
        args.append(int(posted))
    if client_id is not None:
        where.append("e.client_id = ?")
        args.append(client_id)
    sql = (
        "SELECT e.*, c.name AS client_name FROM entries e "
        "LEFT JOIN clients c ON c.id = e.client_id "
    )
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "ORDER BY e.work_date, e.start_time, e.id"
    with closing(connect()) as conn, conn:
        return [dict(r) for r in conn.execute(sql, args)]


def day_totals(month: str) -> dict[str, int]:
    """{'AAAA-MM-DD': minutos} do mês — só apontamentos confirmados contam."""
    with closing(connect()) as conn, conn:
        rows = conn.execute(
            """SELECT work_date, SUM(minutes) AS m FROM entries
               WHERE work_date LIKE ? AND status = 'confirmed'
               GROUP BY work_date""",
            (f"{month}-%",),
        )
        return {r["work_date"]: r["m"] for r in rows}


def month_summary(month: str) -> dict:
    """Totais do mês em minutos: total/posted/unposted (confirmados) + suggested."""
    with closing(connect()) as conn, conn:
        row = conn.execute(
            """SELECT
                 COALESCE(SUM(CASE WHEN status = 'confirmed' THEN minutes END), 0)                AS total,
                 COALESCE(SUM(CASE WHEN status = 'confirmed' AND posted = 1 THEN minutes END), 0) AS posted,
                 COALESCE(SUM(CASE WHEN status = 'confirmed' AND posted = 0 THEN minutes END), 0) AS unposted,
                 COALESCE(SUM(CASE WHEN status = 'suggested' THEN minutes END), 0)                AS suggested
               FROM entries WHERE work_date LIKE ?""",
            (f"{month}-%",),
        ).fetchone()
        return dict(row)


def set_posted(ids: list[int], posted: bool) -> int:
    """Marca/desmarca 'apontado no Multi Dados' em lote; devolve linhas afetadas."""
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            f"UPDATE entries SET posted = ?, posted_at = ?, updated_at = ? "
            f"WHERE id IN ({marks})",
            (int(posted), _now() if posted else None, _now(), *ids),
        )
        return cur.rowcount


# -- sugestões (motor chega na #120; aqui vive a regra de upsert/dedupe) -------

def upsert_suggestion(rec: dict) -> str:
    """Cria/atualiza a sugestão de apontamento de UMA reunião. Núcleo do dedupe.

    Regra do reprocesso: linha inexistente → INSERT status='suggested' ('created');
    ainda 'suggested' → atualiza horários/cliente/descrição, a IA pode ter corrigido
    ('updated'); 'confirmed'/'discarded' → não toca, dado do usuário vence a IA
    ('skipped'). Idempotente e à prova de corrida (índice único parcial).
    """
    key = rec.get("meeting_started_at")
    if not key:
        raise ValueError("sugestão sem meeting_started_at (chave de dedupe)")
    _check_date(rec["work_date"])
    minutes = _minutes_between(rec["start_time"], rec["end_time"])
    now = _now()
    with closing(connect()) as conn, conn:
        row = conn.execute(
            "SELECT id, status FROM entries WHERE meeting_started_at = ?", (key,)
        ).fetchone()
        if row is None:
            try:
                conn.execute(
                    """INSERT INTO entries (work_date, start_time, end_time, minutes,
                           client_id, client_text, project_id, project_text,
                           description, status, origin, meeting_folder,
                           meeting_started_at, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,'suggested','scriba',?,?,?,?)""",
                    (rec["work_date"], rec["start_time"], rec["end_time"], minutes,
                     rec.get("client_id"), rec.get("client_text", ""),
                     rec.get("project_id"), rec.get("project_text", ""),
                     rec.get("description", ""), rec.get("meeting_folder"),
                     key, now, now),
                )
            except sqlite3.IntegrityError:
                # corrida app+CLI: alguém inseriu entre o SELECT e o INSERT
                return "skipped"
            return "created"
        if row["status"] != "suggested":
            return "skipped"
        conn.execute(
            """UPDATE entries SET work_date = ?, start_time = ?, end_time = ?,
                   minutes = ?, client_id = ?, client_text = ?, description = ?,
                   meeting_folder = ?, updated_at = ?
               WHERE id = ?""",
            (rec["work_date"], rec["start_time"], rec["end_time"], minutes,
             rec.get("client_id"), rec.get("client_text", ""),
             rec.get("description", ""), rec.get("meeting_folder"), now, row["id"]),
        )
        return "updated"


# -- clientes, aliases e projetos ---------------------------------------------

def _norm(raw: str) -> str:
    """Normalização de comparação: apara e colapsa espaços (caixa fica p/ NOCASE)."""
    return " ".join((raw or "").split())


def resolve_client(raw: str) -> tuple[int | None, str]:
    """Casa um nome cru (da IA ou digitado) com o cadastro.

    Devolve (id, nome canônico) quando resolve — por clients.name ou por alias,
    ambos NOCASE — e (None, texto normalizado) quando não; o texto cru preservado
    é o que a UI mostra com a oferta de cadastrar cliente/alias.
    """
    text = _norm(raw)
    if not text:
        return None, ""
    with closing(connect()) as conn, conn:
        row = conn.execute(
            "SELECT id, name FROM clients WHERE name = ? AND active = 1", (text,)
        ).fetchone()
        if row is None:
            row = conn.execute(
                """SELECT c.id, c.name FROM client_aliases a
                   JOIN clients c ON c.id = a.client_id
                   WHERE a.alias = ? AND c.active = 1""",
                (text,),
            ).fetchone()
        if row is None:
            return None, text
        return row["id"], row["name"]


def add_client(name: str) -> int:
    """Cadastra um cliente canônico; idempotente por nome (NOCASE)."""
    text = _norm(name)
    if not text:
        raise ValueError("nome de cliente vazio")
    with closing(connect()) as conn, conn:
        row = conn.execute("SELECT id FROM clients WHERE name = ?", (text,)).fetchone()
        if row is not None:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO clients (name, created_at) VALUES (?, ?)", (text, _now())
        )
        return cur.lastrowid


def add_alias(client_id: int, alias: str) -> int:
    """Liga uma grafia alternativa a um cliente; idempotente para o MESMO cliente,
    erro (IntegrityError) se o alias já pertence a outro."""
    text = _norm(alias)
    if not text:
        raise ValueError("alias vazio")
    with closing(connect()) as conn, conn:
        row = conn.execute(
            "SELECT id, client_id FROM client_aliases WHERE alias = ?", (text,)
        ).fetchone()
        if row is not None:
            if row["client_id"] != client_id:
                raise sqlite3.IntegrityError(
                    f"alias {text!r} já pertence ao cliente id={row['client_id']}"
                )
            return row["id"]
        cur = conn.execute(
            "INSERT INTO client_aliases (client_id, alias) VALUES (?, ?)",
            (client_id, text),
        )
        return cur.lastrowid


def list_clients(active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM clients"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY name COLLATE NOCASE"
    with closing(connect()) as conn, conn:
        return [dict(r) for r in conn.execute(sql)]


def list_aliases(client_id: int) -> list[dict]:
    with closing(connect()) as conn, conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM client_aliases WHERE client_id = ? ORDER BY alias COLLATE NOCASE",
            (client_id,),
        )]


def merge_client(src_id: int, dst_id: int) -> None:
    """Funde src em dst: re-aponta apontamentos, aliases e projetos, registra o
    nome antigo como alias do destino e remove o cliente src."""
    if src_id == dst_id:
        raise ValueError("origem e destino da mesclagem são o mesmo cliente")
    with closing(connect()) as conn, conn:
        src = conn.execute("SELECT name FROM clients WHERE id = ?", (src_id,)).fetchone()
        dst = conn.execute("SELECT id FROM clients WHERE id = ?", (dst_id,)).fetchone()
        if src is None or dst is None:
            raise ValueError("cliente de origem ou destino não existe")
        conn.execute("UPDATE entries SET client_id = ? WHERE client_id = ?",
                     (dst_id, src_id))
        conn.execute("UPDATE client_aliases SET client_id = ? WHERE client_id = ?",
                     (dst_id, src_id))
        # projeto com mesmo código no destino: mantém o do destino; o do src cai
        # junto com o cliente (cascade) — UPDATE OR IGNORE pula só os conflitantes.
        conn.execute("UPDATE OR IGNORE projects SET client_id = ? WHERE client_id = ?",
                     (dst_id, src_id))
        conn.execute("INSERT OR IGNORE INTO client_aliases (client_id, alias) VALUES (?, ?)",
                     (dst_id, src["name"]))
        conn.execute("DELETE FROM clients WHERE id = ?", (src_id,))


def add_project(client_id: int, code: str, label: str = "") -> int:
    """Cadastra um código de OS/GAP do cliente; idempotente por (cliente, código)."""
    text = _norm(code)
    if not text:
        raise ValueError("código de projeto vazio")
    with closing(connect()) as conn, conn:
        row = conn.execute(
            "SELECT id FROM projects WHERE client_id = ? AND code = ?",
            (client_id, text),
        ).fetchone()
        if row is not None:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO projects (client_id, code, label) VALUES (?, ?, ?)",
            (client_id, text, label),
        )
        return cur.lastrowid


def list_projects(client_id: int | None = None, active_only: bool = True) -> list[dict]:
    where, args = [], []
    if client_id is not None:
        where.append("client_id = ?")
        args.append(client_id)
    if active_only:
        where.append("active = 1")
    sql = "SELECT * FROM projects"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY code"
    with closing(connect()) as conn, conn:
        return [dict(r) for r in conn.execute(sql, args)]


# -- dias especiais (feriado, férias...) ---------------------------------------

def set_day_note(work_date: str, note: str) -> None:
    """Marca/desmarca um dia especial (note vazio remove)."""
    _check_date(work_date)
    with closing(connect()) as conn, conn:
        if _norm(note):
            conn.execute(
                "INSERT INTO day_notes (work_date, note) VALUES (?, ?) "
                "ON CONFLICT(work_date) DO UPDATE SET note = excluded.note",
                (work_date, _norm(note)),
            )
        else:
            conn.execute("DELETE FROM day_notes WHERE work_date = ?", (work_date,))


def day_notes_month(month: str) -> dict[str, str]:
    with closing(connect()) as conn, conn:
        rows = conn.execute(
            "SELECT work_date, note FROM day_notes WHERE work_date LIKE ? ORDER BY work_date",
            (f"{month}-%",),
        )
        return {r["work_date"]: r["note"] for r in rows}


# -- backup ---------------------------------------------------------------------

def backup(dest_dir: Path | None = None, keep: int = 10) -> Path | None:
    """Snapshot consistente do banco via Connection.backup() + rotação.

    Devolve o caminho gerado, ou None se não há banco (módulo dormente — nunca
    criar arquivo aqui) ou se algo falhou (loga e segue: backup não pode derrubar
    o boot do app). Um arquivo por dia (timesheet-AAAA-MM-DD.db); o do próprio
    dia é reescrito. dest_dir pode apontar para OneDrive: arquivo morto é seguro.
    """
    src_path = _db_path()
    if not src_path.exists():
        return None
    dest = Path(dest_dir) if dest_dir else util.APP_DIR / "backups"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / f"timesheet-{date.today().isoformat()}.db"
        with closing(connect()) as src, closing(sqlite3.connect(str(out))) as dst:
            src.backup(dst)
        # rotação: mantém os `keep` mais novos (nome = data, ordena lexicográfico)
        old = sorted(dest.glob("timesheet-*.db"))[:-keep] if keep > 0 else []
        for f in old:
            try:
                f.unlink()
            except OSError:
                log.warning("backup: não consegui remover %s", f)
        return out
    except Exception:
        log.exception("backup do timesheet.db falhou")
        return None
