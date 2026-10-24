"""Frontier Market Flask app factory."""
from contextlib import closing
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from backend import db, records_api, seed
from backend.features import catalog, collect, company, experts, fde, feedback, refine, rights

FEATURES = (collect, experts, catalog, rights, refine, fde, company, feedback)


def create_app(root, db_path):
    root = Path(root)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db.init_db(db_path)
    seed.ensure_seed(db_path)
    with closing(db.connect(db_path)) as conn:
        for feature in FEATURES:
            if hasattr(feature, "init_schema"):
                feature.init_schema(conn)
        conn.commit()
    app = Flask(__name__, static_folder=None)
    app.config["DB_PATH"] = str(db_path)
    app.register_blueprint(records_api.bp)
    for feature in FEATURES:
        app.register_blueprint(feature.bp)
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
