"""Postgres compatibility layer (EPIC 13.2): a thin connection wrapper over psycopg 3.

The code base is written against sqlite3 (`?` placeholders, sqlite3.Row, a few sqlite-only SQL
forms). When DATABASE_URL is set, db.connect() returns a PgConnection that accepts the same SQL and
translates it per statement, so feature modules need no changes. SQLite stays the default.

Translated: `?` -> `%s` (outside string literals), literal `%` -> `%%`, `INSERT OR IGNORE` ->
`ON CONFLICT DO NOTHING`, `INTEGER PRIMARY KEY AUTOINCREMENT` -> `BIGSERIAL PRIMARY KEY`,
`sqlite_version()` -> `version()`, `json_extract(col, '$.k')` -> `(col::json ->> 'k')`,
`LIKE` -> `ILIKE` (sqlite LIKE is ASCII case-insensitive), `BEGIN IMMEDIATE` -> `BEGIN`, PRAGMA
statements -> no-op. Rows support both `row[0]` and `row["name"]`. Driver errors are re-raised as
sqlite3 exceptions because callers catch `sqlite3.Error`.

Not translatable here: string collation/NULL ordering differences between engines.
"""

import re
import sqlite3

_JSON_EXTRACT = re.compile(r"json_extract\(\s*(\w+)\s*,\s*'\$\.(\w+)'\s*\)", re.I)
_AUTOINC = re.compile(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.I)
_INSERT_OR_IGNORE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", re.I)
_LIKE = re.compile(r"\bLIKE\b", re.I)
_SQLITE_VERSION = re.compile(r"\bsqlite_version\(\)", re.I)
_BEGIN_IMMEDIATE = re.compile(r"^\s*BEGIN\s+(IMMEDIATE|EXCLUSIVE|DEFERRED)\b", re.I)


class NoOp(Exception):
    """Raised by translate_sql for statements that have no Postgres equivalent."""


def _split_literals(sql):
    """Yield (is_literal, text) chunks; single-quoted literals ('' escapes) are kept whole."""
    out, i, n = [], 0, len(sql)
    start = 0
    while i < n:
        if sql[i] == "'":
            if i > start:
                out.append((False, sql[start:i]))
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append((True, sql[i:j + 1]))
            i = start = j + 1
        else:
            i += 1
    if start < n:
        out.append((False, sql[start:]))
    return out


def translate_sql(sql, has_params=False):
    """Return Postgres SQL for one sqlite-flavoured statement, or raise NoOp."""
    stripped = sql.strip()
    if stripped.upper().startswith("PRAGMA"):
        raise NoOp(sql)
    if _BEGIN_IMMEDIATE.match(stripped):
        return "BEGIN"
    text = _AUTOINC.sub("BIGSERIAL PRIMARY KEY", sql)
    text = _SQLITE_VERSION.sub("version()", text)
    text = _JSON_EXTRACT.sub(lambda m: f"({m.group(1)}::json ->> '{m.group(2)}')", text)
    ignore = bool(_INSERT_OR_IGNORE.match(text))
    if ignore:
        text = _INSERT_OR_IGNORE.sub("INSERT INTO", text, count=1)
    parts = []
    for is_literal, chunk in _split_literals(text):
        if is_literal:
            parts.append(chunk.replace("%", "%%") if has_params else chunk)
            continue
        if has_params:
            chunk = chunk.replace("%", "%%")
        chunk = chunk.replace("?", "%s")
        chunk = _LIKE.sub("ILIKE", chunk)
        parts.append(chunk)
    text = "".join(parts)
    if ignore:
        text = text.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return text


class Row:
    """sqlite3.Row look-alike: index, name, keys(), iteration over values, dict(row)."""

    __slots__ = ("_names", "_values")

    def __init__(self, names, values):
        self._names = tuple(names)
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return self._values[self._names.index(key)]
            except ValueError:
                raise IndexError(f"No item with that key: {key}") from None
        return self._values[key]

    def keys(self):
        return list(self._names)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __eq__(self, other):
        return isinstance(other, Row) and self._names == other._names and self._values == other._values

    def __hash__(self):
        return hash((self._names, self._values))

    def __repr__(self):
        return f"Row({dict(zip(self._names, self._values))!r})"


def _row_factory(cursor):
    names = [c.name for c in (cursor.description or [])]
    return lambda values: Row(names, values)


class _EmptyCursor:
    rowcount = 0
    lastrowid = None
    description = None

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __iter__(self):
        return iter(())

    def close(self):
        pass


class PgCursor:
    def __init__(self, conn, cursor, is_insert):
        self._conn = conn
        self._cursor = cursor
        self._is_insert = is_insert

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size=1):
        return self._cursor.fetchmany(size)

    def __iter__(self):
        return iter(self._cursor)

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    @property
    def lastrowid(self):
        """Last serial value of this session (lastval()); None when no sequence was used."""
        if not self._is_insert:
            return None
        try:
            row = self._conn.raw.execute("SELECT lastval()").fetchone()
        except Exception:
            return None
        return row[0] if row else None

    def close(self):
        self._cursor.close()


def _wrap_error(exc, psycopg):
    if isinstance(exc, psycopg.errors.IntegrityError):
        return sqlite3.IntegrityError(str(exc))
    if isinstance(exc, psycopg.errors.OperationalError):
        return sqlite3.OperationalError(str(exc))
    return sqlite3.DatabaseError(str(exc))


class PgConnection:
    is_postgres = True

    def __init__(self, raw, psycopg):
        self.raw = raw
        self._psycopg = psycopg

    def execute(self, sql, params=None):
        has_params = params is not None
        try:
            pg_sql = translate_sql(sql, has_params)
        except NoOp:
            return _EmptyCursor()
        is_insert = pg_sql.lstrip()[:6].upper() == "INSERT"
        try:
            cur = self.raw.execute(pg_sql, tuple(params) if has_params else None)
        except self._psycopg.Error as exc:
            raise _wrap_error(exc, self._psycopg) from exc
        return PgCursor(self, cur, is_insert)

    def executemany(self, sql, seq):
        for params in seq:
            self.execute(sql, params)

    def commit(self):
        pass  # autocommit; transactions are explicit (BEGIN / COMMIT statements)

    def rollback(self):
        pass

    def close(self):
        self.raw.close()


def connect_postgres(url, *, connect_timeout=10):
    """Open an autocommit psycopg connection. Imported lazily so SQLite mode needs no driver."""
    import psycopg

    raw = psycopg.connect(
        url, autocommit=True, row_factory=_row_factory, connect_timeout=connect_timeout,
        prepare_threshold=None,  # Neon's pooled endpoint (pgbouncer, transaction mode)
    )
    return PgConnection(raw, psycopg)
