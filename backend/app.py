"""Frontier Market Flask app factory."""
from contextlib import closing
from pathlib import Path

import os

from flask import Flask, jsonify, send_from_directory

from backend import db, demo, records_api, security, seed
from backend.features import catalog, collect, company, experts, fde, feedback, refine, rights

FEATURES = (collect, experts, catalog, rights, refine, fde, company, feedback)


def create_app(root, db_path, *, env=None, clock=None):
    """Build the app. `env` (default os.environ) and `clock` exist for tests only: a request can
    never change either. FM_PUBLIC_MODE=1 turns on the public demo gate (backend.security)."""
    root = Path(root)
    db_path = Path(db_path)
    cfg = security.Settings.from_env(os.environ if env is None else env)
    if not db.database_url():
        db_path.parent.mkdir(parents=True, exist_ok=True)
    db.init_db(db_path)
    with closing(db.connect(db_path)) as conn:
        with db.init_lock(conn):
            seed.ensure_seed(db_path)
            for feature in FEATURES:
                if hasattr(feature, "init_schema"):
                    feature.init_schema(conn)
            if cfg.public:
                demo.init_schema(conn)
        conn.commit()
    app = Flask(__name__, static_folder=None)
    app.config["DB_PATH"] = str(db_path)
    app.config["FM_SETTINGS"] = cfg
    app.config["FM_CLOCK"] = clock or security.utc_now
    app.before_request(security.gate)
    app.after_request(security.add_headers)
    app.register_blueprint(records_api.bp)
    for feature in FEATURES:
        app.register_blueprint(feature.bp)
    if cfg.public:
        app.register_blueprint(security.bp)
        app.register_blueprint(demo.bp)
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
            version = conn.execute("select sqlite_version()").fetchone()[0]
            postgres = db.is_postgres(conn)
        if postgres:
            return jsonify(status="ok", db_path="postgres", postgres=version)
        return jsonify(status="ok", db_path=app.config["DB_PATH"], sqlite=version)

    return app
