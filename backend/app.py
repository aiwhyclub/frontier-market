"""Frontier Market Flask app factory."""
from contextlib import closing
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from backend import db, records_api, seed


def create_app(root, db_path):
    root = Path(root)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db.init_db(db_path)
    seed.ensure_seed(db_path)
    app = Flask(__name__, static_folder=None)
    app.config["DB_PATH"] = str(db_path)
    app.register_blueprint(records_api.bp)
    static_dir = root / "static"

    @app.get("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(static_dir, name)

    @app.get("/api/health")
    def health():
        with closing(db.connect(app.config["DB_PATH"])) as conn:
            sqlite_version = conn.execute("select sqlite_version()").fetchone()[0]
        return jsonify(status="ok", db_path=app.config["DB_PATH"], sqlite=sqlite_version)

    return app
