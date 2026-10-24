"""백업·복원·트랜잭션 테스트. 모든 DB는 임시 디렉터리의 합성 테이블을 사용합니다.

실제 data/ 아래 DB나 backend/db.py 스키마에는 의존하지 않습니다.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from backend.backup import (
    BackupError,
    backup_db,
    check_integrity,
    list_backups,
    make_backup_name,
    restore_db,
    transaction,
)

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "db_backup.py"

SCHEMA = """
CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
CREATE TABLE versions (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id),
    rev INTEGER NOT NULL
);
CREATE TABLE links (
    version_id INTEGER NOT NULL REFERENCES versions(id),
    target TEXT NOT NULL,
    PRIMARY KEY (version_id, target)
);
"""


def seed(path, wal=False):
    conn = sqlite3.connect(str(path))
    try:
        if wal:
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        conn.executescript(
            """
            INSERT INTO items (id, title) VALUES (1, '합성 원문A'), (2, '합성 원문B');
            INSERT INTO versions (id, item_id, rev) VALUES (10, 1, 1), (11, 1, 2), (20, 2, 1);
            INSERT INTO links (version_id, target) VALUES (11, 'item:2'), (20, 'item:1');
            """
        )
        conn.commit()
    finally:
        conn.close()


def snapshot(path):
    conn = sqlite3.connect(str(path))
    try:
        return {
            "items": conn.execute("SELECT id, title FROM items ORDER BY id").fetchall(),
            "versions": conn.execute("SELECT id, item_id, rev FROM versions ORDER BY id").fetchall(),
            "links": conn.execute("SELECT version_id, target FROM links ORDER BY version_id, target").fetchall(),
        }
    finally:
        conn.close()


def mutate(path):
    """백업 이후의 변경: 행 추가·수정·삭제와 연결 변경."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO items (id, title) VALUES (3, '백업 이후 추가')")
        conn.execute("UPDATE items SET title = '수정됨' WHERE id = 1")
        conn.execute("DELETE FROM links WHERE version_id = 20")
        conn.execute("INSERT INTO versions (id, item_id, rev) VALUES (30, 3, 1)")
        conn.commit()
    finally:
        conn.close()


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def path(self, name):
        return self.dir / name


class TransactionTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.db = self.path("tx.sqlite3")
        seed(self.db)
        self.baseline = snapshot(self.db)

    def test_failed_write_mid_transaction_leaves_nothing(self):
        with self.assertRaises(RuntimeError):
            with transaction(self.db) as conn:
                conn.execute("INSERT INTO items (id, title) VALUES (5, '실패한 저장')")
                conn.execute("INSERT INTO versions (id, item_id, rev) VALUES (50, 5, 1)")
                conn.execute("INSERT INTO links (version_id, target) VALUES (50, 'item:1')")
                raise RuntimeError("중간 실패")

        self.assertEqual(snapshot(self.db), self.baseline)

    def test_failed_statement_rolls_back_earlier_writes(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.db) as conn:
                conn.execute("INSERT INTO items (id, title) VALUES (6, '앞선 쓰기')")
                # 기존 versions.id=10 과 중복되어 무결성 오류가 납니다.
                conn.execute("INSERT INTO versions (id, item_id, rev) VALUES (10, 6, 1)")

        self.assertEqual(snapshot(self.db), self.baseline)

    def test_foreign_key_violation_rolls_back(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.db) as conn:
                conn.execute("INSERT INTO items (id, title) VALUES (7, '고아 버전 대상')")
                conn.execute("INSERT INTO versions (id, item_id, rev) VALUES (70, 999, 1)")

        self.assertEqual(snapshot(self.db), self.baseline)

    def test_successful_transaction_commits_and_releases_lock(self):
        with transaction(self.db) as conn:
            conn.execute("INSERT INTO items (id, title) VALUES (8, '정상 저장')")

        self.assertIn((8, "정상 저장"), snapshot(self.db)["items"])
        # 연결이 닫혔으므로 다른 연결이 즉시 쓰기 잠금을 잡을 수 있어야 합니다.
        other = sqlite3.connect(str(self.db), timeout=0.1, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("ROLLBACK")
        finally:
            other.close()

    def test_connection_is_closed_after_failure(self):
        captured = {}
        real_connect = sqlite3.connect

        def spy(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            captured["conn"] = conn
            return conn

        with mock.patch("backend.backup.sqlite3.connect", side_effect=spy):
            with self.assertRaises(RuntimeError):
                with transaction(self.db):
                    raise RuntimeError("실패")

        with self.assertRaises(sqlite3.ProgrammingError):
            captured["conn"].execute("SELECT 1")


class BackupRestoreTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.db = self.path("live.sqlite3")
        self.backup_dir = self.path("backups")
        seed(self.db)

    def test_backup_mutate_restore_returns_original_rows_versions_and_links(self):
        original = snapshot(self.db)
        backup = backup_db(self.db, self.backup_dir / "before.sqlite3")

        mutate(self.db)
        self.assertNotEqual(snapshot(self.db), original)

        restore_db(backup, self.db)

        restored = snapshot(self.db)
        self.assertEqual(restored, original)
        conn = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            joined = conn.execute(
                "SELECT i.title, v.rev, l.target FROM items i "
                "JOIN versions v ON v.item_id = i.id "
                "LEFT JOIN links l ON l.version_id = v.id "
                "WHERE i.id = 1 ORDER BY v.rev, l.target"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(joined, [("합성 원문A", 1, None), ("합성 원문A", 2, "item:2")])

    def test_restore_leaves_no_temp_files_behind(self):
        backup = backup_db(self.db, self.backup_dir / "clean.sqlite3")
        mutate(self.db)
        restore_db(backup, self.db)

        leftovers = [p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        self.assertEqual(sorted(p.name for p in self.backup_dir.iterdir()), ["clean.sqlite3"])

    def test_wal_database_backup_and_restore_keeps_data(self):
        wal_db = self.path("wal.sqlite3")
        seed(wal_db, wal=True)
        conn = sqlite3.connect(str(wal_db))
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("INSERT INTO items (id, title) VALUES (4, 'WAL에만 있는 행')")
        conn.commit()
        expected = snapshot(wal_db)  # 아직 체크포인트되지 않은 WAL 내용 포함
        backup = backup_db(wal_db, self.backup_dir / "wal.sqlite3")
        conn.close()

        mutate(wal_db)
        restore_db(backup, wal_db)

        self.assertEqual(snapshot(wal_db), expected)

    def test_restore_replaces_stale_wal_sidecar_of_target(self):
        backup = backup_db(self.db, self.backup_dir / "sidecar.sqlite3")
        stale = self.path("target.sqlite3")
        seed(stale, wal=True)
        Path(f"{stale}-wal").write_bytes(b"not a real wal")

        restore_db(backup, stale)

        self.assertEqual(snapshot(stale), snapshot(self.db))
        self.assertFalse(Path(f"{stale}-wal").exists())

    def test_backup_refuses_to_overwrite_existing_file(self):
        dest = self.backup_dir / "same.sqlite3"
        backup_db(self.db, dest)
        with self.assertRaises(FileExistsError):
            backup_db(self.db, dest)

    def test_backup_of_missing_source_is_refused(self):
        with self.assertRaises(FileNotFoundError):
            backup_db(self.path("nope.sqlite3"), self.backup_dir / "x.sqlite3")
        self.assertFalse(self.path("nope.sqlite3").exists())


class CorruptedBackupTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.db = self.path("live.sqlite3")
        seed(self.db)
        self.good_backup = backup_db(self.db, self.path("good.sqlite3"))

    def assert_refused_and_target_untouched(self, bad_backup):
        mutate(self.db)
        before = self.db.read_bytes()
        with self.assertRaises(BackupError):
            restore_db(bad_backup, self.db)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")), [])

    def test_garbage_backup_is_refused(self):
        bad = self.path("garbage.sqlite3")
        bad.write_bytes(b"this is not sqlite" * 200)
        self.assert_refused_and_target_untouched(bad)

    def test_truncated_backup_is_refused(self):
        bad = self.path("truncated.sqlite3")
        bad.write_bytes(self.good_backup.read_bytes()[:4096])
        self.assert_refused_and_target_untouched(bad)

    def test_missing_backup_is_refused(self):
        self.assert_refused_and_target_untouched(self.path("absent.sqlite3"))

    def test_backup_with_damaged_page_fails_integrity_check(self):
        bad = self.path("damaged.sqlite3")
        data = bytearray(self.good_backup.read_bytes())
        # 첫 페이지 이후의 B-tree 페이지 헤더 바이트를 훼손합니다.
        page_size = int.from_bytes(data[16:18], "big") or 65536
        offset = page_size * 2
        data[offset : offset + 8] = b"\xff" * 8
        bad.write_bytes(bytes(data))
        with self.assertRaises(BackupError):
            check_integrity(bad)
        self.assert_refused_and_target_untouched(bad)

    def test_corrupted_source_is_not_turned_into_a_backup(self):
        broken = self.path("broken-live.sqlite3")
        broken.write_bytes(b"garbage" * 1000)
        dest = self.path("should-not-exist.sqlite3")
        with self.assertRaises((BackupError, sqlite3.DatabaseError)):
            backup_db(broken, dest)
        self.assertFalse(dest.exists())


class ListBackupsTests(TempDirCase):
    def test_missing_directory_returns_empty(self):
        self.assertEqual(list_backups(self.path("none")), [])

    def test_newest_first_and_ignores_other_files(self):
        db = self.path("live.sqlite3")
        seed(db)
        folder = self.path("backups")
        early = make_backup_name("frontier-market", datetime(2026, 10, 1, 9, 0, 0))
        late = make_backup_name("frontier-market", datetime(2026, 10, 24, 14, 30, 12))
        backup_db(db, folder / early)
        backup_db(db, folder / late)
        (folder / "notes.txt").write_text("not a backup")
        (folder / ".x.tmp").write_bytes(b"partial")

        self.assertEqual([p.name for p in list_backups(folder)], [late, early])

    def test_backup_name_contains_stem_and_timestamp(self):
        name = make_backup_name("frontier-market", datetime(2026, 10, 24, 14, 30, 12, 5))
        self.assertEqual(name, "frontier-market-20261024-143012-000005.sqlite3")


class CliTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.db = self.path("cli.sqlite3")
        self.backup_dir = self.path("backups")
        seed(self.db)

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=str(self.dir),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_backup_then_restore_with_yes(self):
        original = snapshot(self.db)
        created = self.run_cli("backup", "--db", str(self.db), "--backup-dir", str(self.backup_dir))
        self.assertEqual(created.returncode, 0, created.stderr)
        backup_path = Path(created.stdout.strip())
        self.assertEqual(backup_path.parent, self.backup_dir)

        mutate(self.db)
        restored = self.run_cli("restore", str(backup_path), "--db", str(self.db),
                                "--backup-dir", str(self.backup_dir), "--yes")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(snapshot(self.db), original)

    def test_restore_without_yes_changes_nothing(self):
        backup = backup_db(self.db, self.backup_dir / "keep.sqlite3")
        mutate(self.db)
        changed = snapshot(self.db)

        result = self.run_cli("restore", str(backup), "--db", str(self.db),
                              "--backup-dir", str(self.backup_dir))

        self.assertEqual(result.returncode, 2)
        self.assertEqual(snapshot(self.db), changed)

    def test_restore_keeps_a_pre_restore_copy_of_current_db(self):
        backup = backup_db(self.db, self.backup_dir / "base.sqlite3")
        mutate(self.db)
        current = snapshot(self.db)

        result = self.run_cli("restore", str(backup), "--db", str(self.db),
                              "--backup-dir", str(self.backup_dir), "--yes")

        self.assertEqual(result.returncode, 0, result.stderr)
        safety = [p for p in list_backups(self.backup_dir) if "before-restore" in p.name]
        self.assertEqual(len(safety), 1)
        self.assertEqual(snapshot(safety[0]), current)

    def test_corrupted_backup_via_cli_fails_and_keeps_target(self):
        bad = self.path("bad.sqlite3")
        bad.write_bytes(b"junk" * 500)
        before = self.db.read_bytes()

        result = self.run_cli("restore", str(bad), "--db", str(self.db),
                              "--backup-dir", str(self.backup_dir), "--yes")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
