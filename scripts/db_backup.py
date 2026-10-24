#!/usr/bin/env python3
"""SQLite DB 백업·복원 CLI.

사용법:
  python scripts/db_backup.py backup [--db PATH] [--backup-dir DIR]
  python scripts/db_backup.py restore BACKUP [--db PATH] [--backup-dir DIR] --yes

기본 DB는 data/frontier-market.sqlite3, 백업 위치는 data/backups/ 입니다.
복원은 대상 DB를 교체하므로 --yes가 없으면 실행하지 않습니다.
복원 전에는 현재 DB를 백업 디렉터리에 먼저 복사합니다. 이 복사가 실패하면 복원하지 않습니다.
복원 중에는 앱 서버를 멈춘 상태에서 실행하세요.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.backup import (  # noqa: E402
    BackupError,
    backup_db,
    check_integrity,
    make_backup_name,
    restore_db,
)

DEFAULT_DB = ROOT / "data" / "frontier-market.sqlite3"
DEFAULT_BACKUP_DIR = ROOT / "data" / "backups"


def build_parser():
    parser = argparse.ArgumentParser(description="Frontier Lab Market SQLite 백업·복원")
    sub = parser.add_subparsers(dest="command", required=True)

    p_backup = sub.add_parser("backup", help="DB 백업 생성")
    p_backup.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_backup.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)

    p_restore = sub.add_parser("restore", help="백업으로 DB 복원 (--yes 필요)")
    p_restore.add_argument("backup", type=Path, help="복원할 백업 파일")
    p_restore.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_restore.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    p_restore.add_argument("--yes", action="store_true", help="현재 DB를 덮어쓰는 데 동의")
    return parser


def run_backup(db, backup_dir):
    dest = backup_db(db, backup_dir / make_backup_name(db.stem))
    print(dest)
    return 0


def run_restore(args):
    if not args.yes:
        print("복원은 현재 DB를 교체합니다. 확인했다면 --yes를 붙여 다시 실행하세요.", file=sys.stderr)
        return 2
    check_integrity(args.backup)  # 깨진 백업이면 현재 DB 복사본도 만들지 않습니다.
    if args.db.exists():
        safety = backup_db(args.db, args.backup_dir / make_backup_name(f"{args.db.stem}-before-restore"))
        print(f"복원 전 현재 DB 백업: {safety}")
    restore_db(args.backup, args.db)
    print(f"복원 완료: {args.db} <- {args.backup}")
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "backup":
            return run_backup(args.db, args.backup_dir)
        return run_restore(args)
    except (BackupError, FileNotFoundError, FileExistsError, OSError) as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
