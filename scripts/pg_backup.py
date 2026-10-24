"""Neon/Postgres JSON backup without pg_dump (STORY 13.3): every public table -> one JSON file.

Usage: DATABASE_URL=... python scripts/pg_backup.py backups/neon-YYYYmmddTHHMM.json
"""
import json
import os
import sys
from datetime import datetime, timezone

import psycopg

out = sys.argv[1]
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    tables = [r[0] for r in conn.execute(
        "select table_name from information_schema.tables where table_schema='public' order by 1")]
    dump = {"taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tables": {}}
    for name in tables:
        cur = conn.execute(f'select * from "{name}"')
        cols = [d.name for d in cur.description]
        dump["tables"][name] = [dict(zip(cols, row)) for row in cur.fetchall()]
os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
with open(out, "w") as f:
    json.dump(dump, f, ensure_ascii=False, default=str)
print(out, {k: len(v) for k, v in dump["tables"].items()})
