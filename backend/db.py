"""SQLite access for the record store: connection, migrations by version, record service.

Uses only the standard sqlite3 module. Hashes and versions are kept apart (TRD §3.2):
- material_hash: identity of the real content (type, title, body, fields)
- input_hash: identity of the reviewed input (reserved; null until review exists)
- version: how many times the record row has changed (not a content identity)
"""

import hashlib
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

RECORD_TYPES = {
    "materials": "mat",
    "experts": "exp",
    "works": "wrk",
    "catalog": "cat",
    "projects": "prj",
    "companies": "co",
    "rights": "rgt",
    "feedback": "fb",
}

# (relation, allowed other types, reason shown while the link is absent).
EXPECTED_LINKS = {
    "materials": [("subject", ("rights",), "권리 기록이 없어 학습·평가 이용 범위가 미확인입니다.")],
    "experts": [],
    "works": [
        ("by", ("experts",), "작업을 올린 전문가가 연결되지 않았습니다."),
        ("from", ("materials",), "원문 자료가 연결되지 않았습니다."),
        ("for", ("catalog",), "카탈로그가 아직 없어 연결되지 않았습니다. 카탈로그를 만든 뒤 연결할 수 있습니다."),
    ],
    "catalog": [
        ("from", ("materials",), "근거 자료가 연결되지 않았습니다."),
        ("for", ("works",), "연결된 원시 작업이 없습니다."),
    ],
    "projects": [
        ("catalog", ("catalog",), "필수 카탈로그 연결이 없습니다."),
    ],
    "companies": [
        ("work", ("works",), "생성 당시 참조할 작업이 없습니다."),
        ("project", ("projects",), "필수 프로젝트 연결이 없습니다."),
    ],
    "rights": [
        ("subject", ("materials", "experts"), "권리 대상(자료 또는 전문가)이 연결되지 않았습니다."),
    ],
    "feedback": [
        ("about", ("materials", "works", "catalog", "projects", "companies"),
         "연결 대상이 없습니다. 보완 대상은 나중에 연결할 수 있습니다."),
    ],
}

CURRENT_SCHEMA_VERSION = 1

_TYPE_LIST_SQL = ", ".join(f"'{name}'" for name in RECORD_TYPES)

# Migrations are applied in order, once each, inside a single write transaction.
MIGRATIONS = {
    1: [
        f"""CREATE TABLE records (
            type TEXT NOT NULL CHECK (type IN ({_TYPE_LIST_SQL})),
            id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT,
            synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
            version INTEGER NOT NULL CHECK (version >= 1),
            material_hash TEXT NOT NULL,
            input_hash TEXT,
            body TEXT,
            fields_json TEXT NOT NULL DEFAULT '{{}}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (type, id)
        )""",
        """CREATE TABLE record_revisions (
            type TEXT NOT NULL,
            id TEXT NOT NULL,
            version INTEGER NOT NULL CHECK (version >= 1),
            snapshot_json TEXT NOT NULL,
            material_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (type, id, version),
            FOREIGN KEY (type, id) REFERENCES records (type, id)
        )""",
        """CREATE TABLE record_links (
            from_type TEXT NOT NULL,
            from_id TEXT NOT NULL,
            to_type TEXT NOT NULL,
            to_id TEXT NOT NULL,
            relation TEXT NOT NULL CHECK (length(relation) BETWEEN 1 AND 40),
            created_at TEXT NOT NULL,
            PRIMARY KEY (from_type, from_id, to_type, to_id, relation),
            FOREIGN KEY (from_type, from_id) REFERENCES records (type, id),
            FOREIGN KEY (to_type, to_id) REFERENCES records (type, id)
        )""",
        "CREATE INDEX record_links_to ON record_links (to_type, to_id)",
        """CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
    ],
}


class UnknownRecordType(LookupError):
    pass


class RecordNotFound(LookupError):
    pass


class RecordConflict(Exception):
    pass


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(value):
    # Sorted keys, no extra whitespace, UTF-8 output. Not yet verified against TRD FM-CJSON-1.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def material_hash_of(record_type, title, body, fields):
    payload = {"type": record_type, "title": title, "body": body, "fields": fields}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def database_url():
    """Postgres connection string from DATABASE_URL, or None for the default SQLite file."""
    return (os.environ.get("DATABASE_URL") or "").strip() or None


def is_postgres(conn):
    return bool(getattr(conn, "is_postgres", False))


def connect(db_path):
    """Open a connection with autocommit (transactions are explicit) and FK enforcement.

    With DATABASE_URL set this is a Postgres connection (dbcompat wrapper) and db_path is ignored.
    """
    url = database_url()
    if url:
        from backend import dbcompat
        return dbcompat.connect_postgres(url)
    conn = sqlite3.connect(str(db_path), timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def write_transaction(conn):
    """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK on any exception so no partial write remains."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


@contextmanager
def init_lock(conn):
    """Serialize schema/seed setup across processes (cold starts). No-op for SQLite."""
    if not is_postgres(conn):
        yield
        return
    conn.execute("SELECT pg_advisory_lock(7013001)")
    try:
        yield
    finally:
        conn.execute("SELECT pg_advisory_unlock(7013001)")


def _schema_version(conn):
    if is_postgres(conn):
        has_meta = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = 'metadata'"
        ).fetchone()
    else:
        has_meta = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'metadata'"
        ).fetchone()
    if not has_meta:
        return 0
    row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row else 0


def migrate(conn):
    with write_transaction(conn):
        current = _schema_version(conn)
        if current > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema {current} is newer than this code ({CURRENT_SCHEMA_VERSION})"
            )
        for version in sorted(MIGRATIONS):
            if version <= current:
                continue
            for statement in MIGRATIONS[version]:
                conn.execute(statement)
            set_metadata(conn, "schema_version", str(version))


def init_db(db_path):
    """Create or upgrade the database file. Safe to call on every start; never drops data."""
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        with init_lock(conn):
            migrate(conn)
    finally:
        conn.close()


def get_metadata(conn, key):
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_metadata(conn, key, value):
    conn.execute(
        "INSERT INTO metadata (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def check_type(record_type):
    if record_type not in RECORD_TYPES:
        raise UnknownRecordType(record_type)


def _select_row(conn, record_type, record_id):
    return conn.execute(
        "SELECT * FROM records WHERE type = ? AND id = ?", (record_type, record_id)
    ).fetchone()


def _row_to_item(row):
    fields = json.loads(row["fields_json"])
    core = {
        "id": row["id"],
        "type": row["type"],
        "title": row["title"],
        "status": row["status"],
        "synthetic": bool(row["synthetic"]),
        "version": row["version"],
        "material_hash": row["material_hash"],
        "input_hash": row["input_hash"],
        "body": row["body"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    # Core keys win over any stored field with the same name.
    return {**fields, **core}


def _write_revision(conn, record_type, record_id, version, material_hash, snapshot, created_at):
    conn.execute(
        "INSERT INTO record_revisions (type, id, version, snapshot_json, material_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (record_type, record_id, version, canonical_json(snapshot), material_hash, created_at),
    )


def create_record(conn, record_type, *, title, status, synthetic, body=None, fields=None, record_id=None):
    """Insert a record at version 1. Caller must hold a write transaction."""
    check_type(record_type)
    rid = record_id or f"{RECORD_TYPES[record_type]}_{secrets.token_hex(6)}"
    fields = dict(fields or {})
    mhash = material_hash_of(record_type, title, body, fields)
    now = now_iso()
    conn.execute(
        "INSERT INTO records (type, id, title, status, synthetic, version, material_hash, input_hash, "
        "body, fields_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?, NULL, ?, ?, ?, ?)",
        (record_type, rid, title, status, 1 if synthetic else 0, mhash, body,
         canonical_json(fields), now, now),
    )
    _write_revision(conn, record_type, rid, 1, mhash,
                    {"title": title, "status": status, "body": body, "fields": fields, "input_hash": None},
                    now)
    return rid


_UPDATABLE = {"title", "status", "body", "fields", "input_hash"}


def update_record(conn, record_type, record_id, *, expected_version, changes):
    """Apply changes with an optimistic version check. Caller must hold a write transaction.

    Version grows on any real change. material_hash changes only when title/body/fields change,
    and input_hash is stored as given, so a status-only edit keeps both hashes.
    """
    check_type(record_type)
    unknown = set(changes) - _UPDATABLE
    if unknown:
        raise ValueError(f"not updatable: {sorted(unknown)}")
    row = _select_row(conn, record_type, record_id)
    if row is None:
        raise RecordNotFound(record_id)
    if row["version"] != expected_version:
        raise RecordConflict(f"version {row['version']} != expected {expected_version}")

    old_fields = json.loads(row["fields_json"])
    new = {
        "title": changes.get("title", row["title"]),
        "status": changes.get("status", row["status"]),
        "body": changes.get("body", row["body"]),
        "fields": changes.get("fields", old_fields),
        "input_hash": changes.get("input_hash", row["input_hash"]),
    }
    old = {
        "title": row["title"], "status": row["status"], "body": row["body"],
        "fields": old_fields, "input_hash": row["input_hash"],
    }
    if new == old:
        return get_record(conn, record_type, record_id)

    new["fields"] = dict(new["fields"] or {})
    mhash = material_hash_of(record_type, new["title"], new["body"], new["fields"])
    version = row["version"] + 1
    now = now_iso()
    conn.execute(
        "UPDATE records SET title = ?, status = ?, body = ?, fields_json = ?, input_hash = ?, "
        "material_hash = ?, version = ?, updated_at = ? WHERE type = ? AND id = ?",
        (new["title"], new["status"], new["body"], canonical_json(new["fields"]), new["input_hash"],
         mhash, version, now, record_type, record_id),
    )
    _write_revision(conn, record_type, record_id, version, mhash, new, now)
    return get_record(conn, record_type, record_id)


def get_record(conn, record_type, record_id):
    check_type(record_type)
    row = _select_row(conn, record_type, record_id)
    return _row_to_item(row) if row else None


def list_records(conn, record_type, *, limit=50, offset=0):
    check_type(record_type)
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM records WHERE type = ?", (record_type,)
    ).fetchone()["n"]
    rows = conn.execute(
        "SELECT * FROM records WHERE type = ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (record_type, limit, offset),
    ).fetchall()
    items = [
        {
            "id": r["id"],
            "type": r["type"],
            "title": r["title"],
            "status": r["status"],
            "synthetic": bool(r["synthetic"]),
            "version": r["version"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]
    return items, total


def counts(conn):
    result = {name: 0 for name in RECORD_TYPES}
    for row in conn.execute("SELECT type, COUNT(*) AS n FROM records GROUP BY type"):
        result[row["type"]] = row["n"]
    return result


def add_link(conn, from_type, from_id, to_type, to_id, relation):
    """Store a directed link. Foreign keys reject links to records that do not exist.

    Re-adding an identical link is a no-op (INSERT OR IGNORE), which keeps seeding safe.
    """
    conn.execute(
        "INSERT OR IGNORE INTO record_links (from_type, from_id, to_type, to_id, relation, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (from_type, from_id, to_type, to_id, relation, now_iso()),
    )


def relations(conn, record_type, record_id):
    """Return (links, missing_links) for one record. A missing link carries the reason to show."""
    check_type(record_type)
    rows = conn.execute(
        "SELECT l.from_type, l.from_id, l.to_type, l.to_id, l.relation, "
        "       rf.title AS from_title, rt.title AS to_title "
        "FROM record_links l "
        "JOIN records rf ON rf.type = l.from_type AND rf.id = l.from_id "
        "JOIN records rt ON rt.type = l.to_type AND rt.id = l.to_id "
        "WHERE (l.from_type = ? AND l.from_id = ?) OR (l.to_type = ? AND l.to_id = ?) "
        "ORDER BY l.relation, l.created_at, l.from_id, l.to_id",
        (record_type, record_id, record_type, record_id),
    ).fetchall()

    links = []
    for r in rows:
        if r["from_type"] == record_type and r["from_id"] == record_id:
            links.append({"type": r["to_type"], "id": r["to_id"], "title": r["to_title"],
                          "relation": r["relation"], "direction": "out"})
        else:
            links.append({"type": r["from_type"], "id": r["from_id"], "title": r["from_title"],
                          "relation": r["relation"], "direction": "in"})

    missing = []
    for relation, other_types, reason in EXPECTED_LINKS[record_type]:
        present = any(l["relation"] == relation and l["type"] in other_types for l in links)
        if not present:
            missing.append({"type": "|".join(other_types), "relation": relation, "reason": reason})
    return links, missing


# --- Idempotent creates (EPIC 11.4) -------------------------------------------------
# A client may send an Idempotency-Key header on create requests. The first request creates the
# record and remembers (scope, key) -> record id plus a fingerprint of the payload, in `metadata`.
# A retry with the same key and payload returns the same record and writes nothing; the same key
# with a different payload is refused. Call these helpers inside the caller's write transaction.

_IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9._-]{8,128}")


class IdempotencyConflict(Exception):
    pass


def normalize_idempotency_key(raw):
    """Return the stripped key, None when absent, or raise ValueError when malformed."""
    if raw is None:
        return None
    key = raw.strip()
    if not _IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise ValueError("Idempotency-Key must be 8-128 characters of letters, digits, '.', '_' or '-'")
    return key


def _idem_row_key(scope, key):
    return f"idem:{scope}:{key}"


def _payload_fingerprint(payload):
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def replay_idempotent(conn, scope, key, payload):
    """Return (record_type, record_id) if this key already created a record, else None.

    Raises IdempotencyConflict if the key was used before with a different payload.
    """
    stored = get_metadata(conn, _idem_row_key(scope, key))
    if stored is None:
        return None
    record_type, record_id, fingerprint = stored.split("|", 2)
    if fingerprint != _payload_fingerprint(payload):
        raise IdempotencyConflict(key)
    return record_type, record_id


def remember_idempotent(conn, scope, key, payload, record_type, record_id):
    set_metadata(conn, _idem_row_key(scope, key),
                 f"{record_type}|{record_id}|{_payload_fingerprint(payload)}")
