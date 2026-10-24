"""EPIC 13.2: SQL translation and the Postgres wrapper. No Postgres server is needed here.

The wrapper is exercised against a recording fake driver; the real Postgres runtime is unverified
(see docs/deploy.md). Set TEST_DATABASE_URL to run test_real_postgres against a disposable database.
"""
import os
import sqlite3
import unittest
from unittest import mock

from backend import db, dbcompat
from backend.dbcompat import NoOp, PgConnection, Row, translate_sql


class TranslateTest(unittest.TestCase):
    def test_placeholders_outside_literals_only(self):
        self.assertEqual(translate_sql("SELECT * FROM t WHERE a = ? AND b = '?' AND c = ?", True),
                         "SELECT * FROM t WHERE a = %s AND b = '?' AND c = %s")

    def test_percent_is_escaped_only_with_params(self):
        self.assertEqual(translate_sql("SELECT '100%' WHERE a = ?", True), "SELECT '100%%' WHERE a = %s")
        self.assertEqual(translate_sql("SELECT '100%'", False), "SELECT '100%'")

    def test_quote_escape_inside_literal(self):
        self.assertEqual(translate_sql("SELECT 'it''s ?', ?", True), "SELECT 'it''s ?', %s")

    def test_insert_or_ignore(self):
        self.assertEqual(
            translate_sql("INSERT OR IGNORE INTO l (a, b) VALUES (?, ?)", True),
            "INSERT INTO l (a, b) VALUES (%s, %s) ON CONFLICT DO NOTHING")

    def test_autoincrement_and_sqlite_version_and_json_extract(self):
        self.assertIn("BIGSERIAL PRIMARY KEY",
                      translate_sql("CREATE TABLE x (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)"))
        self.assertEqual(translate_sql("select sqlite_version()"), "select version()")
        self.assertEqual(translate_sql("WHERE json_extract(fields_json, '$.domain') = ?", True),
                         "WHERE (fields_json::json ->> 'domain') = %s")

    def test_like_becomes_ilike_but_not_inside_literals(self):
        self.assertEqual(translate_sql("a LIKE ? ESCAPE '\\' AND b = 'LIKE'", True),
                         "a ILIKE %s ESCAPE '\\' AND b = 'LIKE'")

    def test_begin_immediate_and_pragma(self):
        self.assertEqual(translate_sql("BEGIN IMMEDIATE"), "BEGIN")
        self.assertEqual(translate_sql("COMMIT"), "COMMIT")
        with self.assertRaises(NoOp):
            translate_sql("PRAGMA foreign_keys = ON")

    def test_every_migration_and_core_statement_translates(self):
        for statements in db.MIGRATIONS.values():
            for stmt in statements:
                out = translate_sql(stmt)
                self.assertNotIn("AUTOINCREMENT", out)
                self.assertNotIn("?", out)


class FakeCursor:
    def __init__(self, rows=(), rowcount=1):
        self._rows, self.rowcount, self.description = list(rows), rowcount, None

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeRaw:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.error = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if self.error:
            raise self.error
        if sql.startswith("SELECT lastval"):
            return FakeCursor([(42,)])
        return FakeCursor([Row(["n"], [3])])

    def close(self):
        self.closed = True


class FakePsycopg:
    class Error(Exception):
        pass

    class errors:
        pass


class WrapperTest(unittest.TestCase):
    def setUp(self):
        self.raw = FakeRaw()
        self.conn = PgConnection(self.raw, _FP)

    def test_execute_translates_and_passes_params(self):
        row = self.conn.execute("SELECT COUNT(*) AS n FROM records WHERE type = ?", ("works",)).fetchone()
        self.assertEqual(self.raw.calls[-1], ("SELECT COUNT(*) AS n FROM records WHERE type = %s", ("works",)))
        self.assertEqual((row["n"], row[0]), (3, 3))

    def test_pragma_is_a_noop_and_never_reaches_the_driver(self):
        cur = self.conn.execute("PRAGMA journal_mode = WAL")
        self.assertEqual(cur.fetchall(), [])
        self.assertEqual(self.raw.calls, [])

    def test_lastrowid_after_insert_uses_lastval(self):
        cur = self.conn.execute("INSERT INTO company_events (kind) VALUES (?)", ("revoke",))
        self.assertEqual(cur.lastrowid, 42)
        self.assertIsNone(self.conn.execute("SELECT 1").lastrowid)

    def test_driver_errors_become_sqlite3_errors(self):
        self.raw.error = _FP.errors.IntegrityError("dup")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO t VALUES (?)", (1,))
        self.raw.error = _FP.Error("boom")
        with self.assertRaises(sqlite3.Error):
            self.conn.execute("SELECT 1")

    def test_write_transaction_uses_begin_commit_rollback(self):
        with db.write_transaction(self.conn):
            pass
        self.assertEqual([c[0] for c in self.raw.calls], ["BEGIN", "COMMIT"])
        self.raw.calls.clear()
        with self.assertRaises(RuntimeError):
            with db.write_transaction(self.conn):
                raise RuntimeError("x")
        self.assertEqual([c[0] for c in self.raw.calls], ["BEGIN", "ROLLBACK"])


class _FP(FakePsycopg):
    class errors:
        class IntegrityError(FakePsycopg.Error):
            pass

        class OperationalError(FakePsycopg.Error):
            pass


class RowTest(unittest.TestCase):
    def test_row_matches_sqlite_row_usage(self):
        row = Row(["id", "title"], ["a", "T"])
        self.assertEqual((row[0], row["title"], row.keys(), dict(row), list(row), len(row)),
                         ("a", "T", ["id", "title"], {"id": "a", "title": "T"}, ["a", "T"], 2))
        with self.assertRaises(IndexError):
            row["missing"]


class SwitchTest(unittest.TestCase):
    def test_default_is_sqlite_and_database_url_selects_postgres(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            conn = db.connect(":memory:")
            self.assertFalse(db.is_postgres(conn))
            conn.close()
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@h/db"}):
            with mock.patch.object(dbcompat, "connect_postgres", return_value="PG") as m:
                self.assertEqual(db.connect("ignored.sqlite3"), "PG")
                m.assert_called_once_with("postgresql://u:p@h/db")

    @unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "needs a disposable Postgres (TEST_DATABASE_URL)")
    def test_real_postgres(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": os.environ["TEST_DATABASE_URL"]}):
            db.init_db("ignored")
            with db.write_transaction(db.connect("ignored")) as conn:
                self.assertIsNotNone(db.get_metadata(conn, "schema_version"))


if __name__ == "__main__":
    unittest.main()
