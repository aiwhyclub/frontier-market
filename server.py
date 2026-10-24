#!/usr/bin/env python3
"""Frontier Lab Market local prototype (Flask)."""
import argparse
import os
from pathlib import Path

from backend.app import create_app


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            if key and key.replace("_", "").isalnum():
                os.environ.setdefault(key, value.strip().strip("\"'"))


def main():
    root = Path(__file__).resolve().parent
    load_env(root / ".env")
    parser = argparse.ArgumentParser(description="Frontier Lab Market local prototype")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8787")))
    parser.add_argument("--db", default=os.getenv("DB_PATH", str(root / "data" / "frontier-market.sqlite3")))
    args = parser.parse_args()
    app = create_app(root, args.db)
    print(f"Frontier Lab Market: http://127.0.0.1:{args.port}  (DB: {args.db})", flush=True)
    print("Local prototype • synthetic demo data • Ctrl+C to stop", flush=True)
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
