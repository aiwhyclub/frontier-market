"""SQLite 트랜잭션·백업·복원 기반 (TRD §8.4).

- transaction(): BEGIN IMMEDIATE로 쓰기 잠금을 잡고, 성공 시 COMMIT, 실패 시 ROLLBACK 후
  항상 close합니다. sqlite3의 `with connection`은 close를 하지 않으므로 쓰지 않습니다.
- backup_db(): Connection.backup API로 일관된 복사본을 만듭니다. WAL 사용 중 DB 파일을
  직접 복사하지 않습니다. 복사본은 임시 파일에서 무결성 검사 후 원자적으로 이름을 바꿉니다.
- restore_db(): 백업의 무결성을 먼저 검사하고, 통과한 경우에만 대상 DB를 교체합니다.
"""

import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

BUSY_TIMEOUT_SECONDS = 5.0
BACKUP_SUFFIX = ".sqlite3"


class BackupError(Exception):
    """백업 생성·검사·복원이 안전하게 끝나지 않았을 때 발생합니다."""


@contextmanager
def transaction(db_path):
    """BEGIN IMMEDIATE 트랜잭션. 본문이 예외를 내면 롤백하고, 어떤 경우든 연결을 닫습니다."""
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            _rollback_if_open(conn)
            raise
        try:
            conn.execute("COMMIT")
        except BaseException:
            _rollback_if_open(conn)
            raise
    finally:
        conn.close()


def _rollback_if_open(conn):
    if conn.in_transaction:
        conn.execute("ROLLBACK")


def check_integrity(path):
    """PRAGMA integrity_check이 'ok'가 아니면 BackupError를 냅니다."""
    path = Path(path)
    if not path.is_file():
        raise BackupError(f"파일이 없습니다: {path}")
    conn = None
    try:
        conn = sqlite3.connect(str(path))
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"SQLite 파일을 읽을 수 없습니다: {path} ({exc})") from exc
    finally:
        if conn is not None:
            conn.close()
    if rows != [("ok",)]:
        raise BackupError(f"무결성 검사 실패: {path} ({rows[:3]})")


def make_backup_name(stem, now=None):
    """정렬 가능한 타임스탬프 파일명. 예: frontier-market-20261024-143012-000123.sqlite3"""
    now = now or datetime.now()
    return f"{stem}-{now:%Y%m%d-%H%M%S}-{now.microsecond:06d}{BACKUP_SUFFIX}"


def list_backups(directory):
    """백업 디렉터리의 *.sqlite3 파일을 최신순(파일명 내림차순)으로 돌려줍니다."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted((p for p in directory.glob(f"*{BACKUP_SUFFIX}") if p.is_file()), reverse=True)


def backup_db(src_path, dest_path):
    """src의 일관된 복사본을 dest에 만듭니다. dest가 이미 있으면 덮어쓰지 않습니다."""
    src_path = Path(src_path)
    dest_path = Path(dest_path)
    if not src_path.is_file():
        raise FileNotFoundError(f"백업할 DB가 없습니다: {src_path}")
    if dest_path.exists():
        raise FileExistsError(f"이미 있는 백업 파일입니다: {dest_path}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temp_sibling(dest_path)
    try:
        _copy_via_backup_api(src_path, tmp)
        check_integrity(tmp)
        os.replace(tmp, dest_path)
    except BaseException:
        _remove_quietly(tmp)
        raise
    return dest_path


def restore_db(backup_path, target_path):
    """백업을 검사한 뒤 target_path를 교체합니다. 검사에 실패하면 target은 건드리지 않습니다."""
    backup_path = Path(backup_path)
    target_path = Path(target_path)
    check_integrity(backup_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temp_sibling(target_path)
    try:
        _copy_via_backup_api(backup_path, tmp)
        check_integrity(tmp)
        # 이전 DB의 WAL/SHM이 새 파일에 적용되지 않도록 교체 전에 제거합니다.
        for suffix in ("-wal", "-shm"):
            Path(f"{target_path}{suffix}").unlink(missing_ok=True)
        os.replace(tmp, target_path)
    except BaseException:
        _remove_quietly(tmp)
        raise
    return target_path


def _copy_via_backup_api(src_path, dest_path):
    src = sqlite3.connect(str(src_path))
    try:
        dest = sqlite3.connect(str(dest_path))
        try:
            src.backup(dest)
            # 복사본은 단일 파일로 남기기 위해 rollback journal 모드로 둡니다.
            dest.execute("PRAGMA journal_mode = DELETE")
        finally:
            dest.close()
    finally:
        src.close()


def _temp_sibling(path):
    return path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")


def _remove_quietly(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
