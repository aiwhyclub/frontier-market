"""Shared helpers for EPIC 12 AI drafts (F10.4~F10.6): run log, JSON draft parsing, UI labels.

A draft is never saved as the record itself. The attempt (provider, model, start/end, ok, error,
usage, input_hash; never the API key or request text) goes to `ai_draft_runs`; a person adopts or
edits the draft through the feature's normal save path.
"""

import json
import uuid

from backend import ai, db

SCHEMA = """CREATE TABLE IF NOT EXISTS ai_draft_runs (
    id TEXT PRIMARY KEY, feature TEXT NOT NULL, subject_type TEXT NOT NULL, subject_id TEXT,
    attempted INTEGER NOT NULL, provider TEXT, model TEXT, started_at TEXT, finished_at TEXT,
    ok INTEGER NOT NULL DEFAULT 0, error TEXT, usage_json TEXT, input_hash TEXT NOT NULL,
    mode TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}', adopted_at TEXT, created_at TEXT NOT NULL)"""


def init_schema(conn):
    conn.execute(SCHEMA)


def label(model):
    return f"AI 초안 ({model})" if model else "수동 작성"


def parse_json(text):
    """First JSON object in the model text, or None."""
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def draft(conn, feature, system, prompt, subject_type, subject_id, detail=None):
    """Make one attempt and record it. Returns (parsed_dict_or_None, public_run_info)."""
    text, run = ai.attempt(feature, system, prompt)
    parsed = parse_json(text) if text else None
    error = run.get("error")
    if text and parsed is None:
        error = "unparseable_response"
    run_id = "air_" + uuid.uuid4().hex[:12]
    usage = run.get("usage")
    conn.execute(
        "INSERT INTO ai_draft_runs (id, feature, subject_type, subject_id, attempted, provider, model, "
        "started_at, finished_at, ok, error, usage_json, input_hash, mode, detail_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, feature, subject_type, subject_id, 1 if run.get("attempted", False) else 0,
         run.get("provider"), run.get("model"), run.get("started_at"), run.get("finished_at"),
         1 if (run.get("ok") and parsed is not None) else 0, error,
         json.dumps(usage) if usage is not None else None, run["input_hash"],
         "ai" if parsed is not None else "rule_based",
         json.dumps(detail or {}, ensure_ascii=False), db.now_iso()))
    ok = parsed is not None
    info = {"run_id": run_id, "attempted": bool(run.get("attempted", False)), "ok": ok,
            "model": run.get("model") if run.get("attempted") else None, "error": error,
            "label": label(run.get("model")) if ok else label(None), "mode": "ai" if ok else "manual"}
    return parsed, info


def get_run(conn, run_id, feature):
    row = conn.execute("SELECT * FROM ai_draft_runs WHERE id=? AND feature=?", (run_id, feature)).fetchone()
    if row is None:
        return None
    run = dict(row)
    run["detail"] = json.loads(run.pop("detail_json") or "{}")
    return run


def mark_adopted(conn, run_id):
    conn.execute("UPDATE ai_draft_runs SET adopted_at=? WHERE id=?", (db.now_iso(), run_id))


def status(feature):
    s = ai.status(feature)
    s["label"] = label(s["model"]) if s["adopted"] else "수동 작성 (AI 미채택)"
    return s


def clip(value, limit):
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) else ""
