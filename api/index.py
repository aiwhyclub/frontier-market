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

flask_app = create_app(ROOT, os.environ.get("DB_PATH") or "/tmp/frontier-market.sqlite3")


def app(environ, start_response):
    """vercel.json rewrites every path to /api/index?__fm_path=/<path>; restore it for Flask.

    A client sending __fm_path itself only reaches the same path through the same auth checks.
    """
    from urllib.parse import parse_qsl, urlencode
    pairs = parse_qsl(environ.get("QUERY_STRING", ""), keep_blank_values=True)
    original = [v for k, v in pairs if k == "__fm_path"]
    if original:
        environ["PATH_INFO"] = original[-1] or "/"
        environ["QUERY_STRING"] = urlencode([(k, v) for k, v in pairs if k != "__fm_path"])
    return flask_app(environ, start_response)
