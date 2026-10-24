"""Vercel entry point (EPIC 13.2): exposes the Flask WSGI app as `app`. No dev server here.

Production needs DATABASE_URL (Neon Postgres) and the public-mode settings in docs/deploy.md.
Without DATABASE_URL on Vercel the filesystem is ephemeral, so refuse to start instead of
silently serving a throwaway SQLite file.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app import create_app  # noqa: E402

if os.environ.get("VERCEL") and not (os.environ.get("DATABASE_URL") or "").strip():
    raise RuntimeError("DATABASE_URL is required on Vercel (no persistent disk).")

app = create_app(ROOT, os.environ.get("DB_PATH") or "/tmp/frontier-market.sqlite3")
