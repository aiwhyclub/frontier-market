"""Public demo API (EPIC 13.2): synthetic read + per-visitor task run/reset. Only /api/demo/*.

Registered only in public mode. The request gate (backend.security) has already checked the end
time, the visitor cookie and the CSRF token before any function here runs. The visitor key
(demo_session_ref) is derived on the server from the signed cookie; no request field names it.

Deviation from TRD §3: visitor attempts live in their own `demo_runs` / `demo_run_history` /
`demo_receipts` tables instead of extra columns on company_runs, so the operator run tables keep
their schema and are never read or written by a public request. Semantics follow TRD §8.4 / §9.2:
run_version is 0 until the first stored attempt, 1 at the first run/reset, +1 per stored change.
"""

import hashlib
import json
import sqlite3
from contextlib import closing

from flask import Blueprint, current_app, g, jsonify, make_response, request

from backend import db, security
from backend.features import company

bp = Blueprint("demo", __name__, url_prefix="/api/demo")

ENTITIES = {"sources": "materials", "experts": "experts", "catalogs": "catalog",
            "works": "works", "projects": "projects", "companies": "companies"}
PUBLIC_FIELDS = ("id", "type", "title", "status", "synthetic", "version", "material_hash",
                 "body", "updated_at")
MAX_EVENTS_PER_VISITOR = 300
INITIAL_RUN = {"attempt": 1, "run_version": 0, "state": "pending_review",
               "inspection_complete": False, "score": None, "started": False}

SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS demo_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT NOT NULL, task_id TEXT NOT NULL,
        demo_session_ref TEXT NOT NULL, attempt INTEGER NOT NULL, run_version INTEGER NOT NULL,
        is_current INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL,
        inspection_complete INTEGER NOT NULL DEFAULT 0, score INTEGER, updated_at TEXT NOT NULL)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS demo_runs_current ON demo_runs "
    "(company_id, task_id, demo_session_ref) WHERE is_current = 1",
    "CREATE UNIQUE INDEX IF NOT EXISTS demo_runs_attempt ON demo_runs "
    "(company_id, task_id, demo_session_ref, attempt)",
    """CREATE TABLE IF NOT EXISTS demo_run_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT NOT NULL, task_id TEXT NOT NULL,
        demo_session_ref TEXT NOT NULL, attempt INTEGER NOT NULL, run_version INTEGER NOT NULL,
        event TEXT NOT NULL, from_state TEXT, to_state TEXT, score INTEGER,
        detail_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS demo_history_ref ON demo_run_history (demo_session_ref, company_id, task_id)",
    """CREATE TABLE IF NOT EXISTS demo_receipts (
        request_scope TEXT NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
        response_json TEXT NOT NULL, http_status INTEGER NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (request_scope, request_id))""",
]


def init_schema(conn):
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)


def _conn():
    return db.connect(current_app.config["DB_PATH"])


def _error(status, code, message, **extra):
    return jsonify(error={"code": code, "message": message}, **extra), status


def _fresh_session():
    """Issue a new visitor cookie when the request has no valid one."""
    if g.visitor is not None:
        return g.visitor, None
    value, payload = security.issue("visitor", security.settings().visitor_ttl)
    return payload, value


def _respond(payload, status=200):
    """Attach the visitor cookie to the response when one was just issued."""
    response = make_response(jsonify(payload), status)
    value = getattr(g, "new_visitor_cookie", None)
    if value:
        security.set_cookie(response, security.VISITOR_COOKIE, value, security.settings().visitor_ttl)
    return response


@bp.before_request
def _ensure_session_on_read():
    # Reads without a valid session get a fresh one (nothing is stored for it until a run/reset).
    if request.method == "GET":
        g.visitor, g.new_visitor_cookie = _fresh_session()


def _public_item(rec):
    return {k: rec[k] for k in PUBLIC_FIELDS if k in rec}


def _task_run(conn, cid, tid, ref):
    row = conn.execute(
        "SELECT * FROM demo_runs WHERE company_id=? AND task_id=? AND demo_session_ref=? AND is_current=1",
        (cid, tid, ref)).fetchone()
    return row


def _run_dict(row):
    if row is None:
        return dict(INITIAL_RUN)
    return {"attempt": row["attempt"], "run_version": row["run_version"], "state": row["state"],
            "inspection_complete": bool(row["inspection_complete"]), "score": row["score"],
            "started": True}


def _history(conn, cid, tid, ref):
    rows = conn.execute(
        "SELECT attempt, run_version, event, from_state, to_state, score, created_at FROM demo_run_history "
        "WHERE company_id=? AND task_id=? AND demo_session_ref=? ORDER BY id", (cid, tid, ref)).fetchall()
    return [dict(r) for r in rows]


def _company_state(conn, cid):
    """Return (record, package, gate) for a synthetic company, or (None, None, None)."""
    record = db.get_record(conn, "companies", cid)
    if record is None or not record["synthetic"]:
        return None, None, None
    row = conn.execute("SELECT * FROM company_packages WHERE company_id=?", (cid,)).fetchone()
    if row is None:
        return record, None, None
    pkg = json.loads(row["snapshot_text"])
    return record, pkg, {"gate": company._export_gate(conn, pkg, db.now_iso()), "sha256": row["snapshot_sha256"]}


def _company_view(conn, cid, ref):
    record, pkg, info = _company_state(conn, cid)
    if record is None:
        return None
    view = {"id": cid, "title": record["title"], "purpose": record.get("purpose"),
            "synthetic": True, "tasks": [], "hold": False, "holds": [], "package_sha256": None}
    if pkg is None:
        return view
    gate = info["gate"]
    view["package_sha256"] = info["sha256"]
    view["hold"] = not gate["allowed"]
    view["holds"] = [{"reason": h["reason"], "action": h["action"]} for h in gate["holds"]]
    view["criteria"] = pkg["criteria"]
    view["environment_definition"] = pkg["environment_definition"]
    for task in pkg["tasks"]:
        tid = task["task_id"]
        view["tasks"].append({"task_id": tid, "title": task["title"], "criteria": task["criteria"],
                              "run": _run_dict(_task_run(conn, cid, tid, ref)),
                              "history": _history(conn, cid, tid, ref)})
    return view


@bp.get("/status")
def status():
    """Tells the static UI it is running in public mode (404 here means local mode). The gate has
    already answered 410 `closed` after the end time, so reaching this means the demo is open."""
    return _respond({"public": True, "closed": False,
                     "public_end": security.settings().end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "open_operator": security.open_operator()})


@bp.get("/bootstrap")
def bootstrap():
    payload = g.visitor
    with closing(_conn()) as conn:
        companies = []
        for r in conn.execute("SELECT id FROM records WHERE type='companies' AND synthetic=1 "
                              "ORDER BY created_at DESC, id").fetchall():
            rec = db.get_record(conn, "companies", r["id"])
            companies.append({"id": rec["id"], "title": rec["title"], "purpose": rec.get("purpose")})
        counts = {name: conn.execute("SELECT COUNT(*) AS n FROM records WHERE type=? AND synthetic=1",
                                     (rtype,)).fetchone()["n"] for name, rtype in ENTITIES.items()}
    return _respond({"csrf_token": security.csrf_token("visitor", payload),
                     "session_expires_at": payload["exp"],
                     "public_end": security.settings().end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "synthetic_only": True, "companies": companies, "counts": counts})


@bp.get("/<entity>")
def list_entity(entity):
    rtype = ENTITIES.get(entity)
    if rtype is None:
        return _error(404, "not_found", "공개 대상이 아닙니다.")
    with closing(_conn()) as conn:
        rows = conn.execute("SELECT id FROM records WHERE type=? AND synthetic=1 "
                            "ORDER BY created_at DESC, id DESC LIMIT 100", (rtype,)).fetchall()
        items = [_public_item(db.get_record(conn, rtype, r["id"])) for r in rows]
    for item in items:
        item.pop("body", None)
    return _respond({"items": items, "total": len(items)})


@bp.get("/<entity>/<rid>")
def get_entity(entity, rid):
    rtype = ENTITIES.get(entity)
    if rtype is None:
        return _error(404, "not_found", "공개 대상이 아닙니다.")
    with closing(_conn()) as conn:
        if rtype == "companies":
            view = _company_view(conn, rid, security.visitor_ref(g.visitor))
            if view is None:
                return _error(404, "not_found", "찾을 수 없습니다.")
            return _respond({"item": view, "run_scope": "visitor"})
        rec = db.get_record(conn, rtype, rid)
    if rec is None or not rec["synthetic"]:
        return _error(404, "not_found", "찾을 수 없습니다.")
    return _respond({"item": _public_item(rec)})


# --- run / reset ---------------------------------------------------------------------------


def _request_hash(op, cid, body):
    return hashlib.sha256(db.canonical_json({"op": op, "company": cid, "body": body}).encode()).hexdigest()


def _record_event(conn, cid, tid, ref, attempt, version, event, from_state, to_state, score, detail, now):
    conn.execute(
        "INSERT INTO demo_run_history (company_id, task_id, demo_session_ref, attempt, run_version, event, "
        "from_state, to_state, score, detail_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, tid, ref, attempt, version, event, from_state, to_state, score,
         json.dumps(detail, ensure_ascii=False, sort_keys=True), now))


def _inspect(conn, pkg, task, gate_allowed):
    answer = next((a for a in pkg["answers"] if a["task_id"] == task["task_id"]), {})
    experts = [db.get_record(conn, "experts", p["id"]) or {} for p in pkg["provenance"] if p["type"] == "experts"]
    checks = {"answer_present": bool(answer.get("answer")),
              "provenance_linked": any(p["id"] == task["work_id"] for p in pkg["provenance"]),
              "rights_ok": gate_allowed,
              "reviewer_ok": all(company._expert_approved(e) for e in experts)}
    return checks, sum(1 for v in checks.values() if v)


def _validate_body(body, with_action):
    if not isinstance(body, dict):
        return "JSON 객체를 보내세요."
    if not isinstance(body.get("task_id"), str) or not body["task_id"]:
        return "task_id가 필요합니다."
    version = body.get("expected_run_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        return "expected_run_version(0 이상의 정수)이 필요합니다."
    if with_action and body.get("action") not in company.RUN_ACTIONS:
        return "action은 request_inspection/hold/release 중 하나입니다."
    return None


def _mutate(cid, op):
    body = request.get_json(silent=True)
    problem = _validate_body(body, op == "run")
    if problem:
        return _error(400, "bad_request", problem)
    try:
        key = db.normalize_idempotency_key(request.headers.get("Idempotency-Key"))
    except ValueError as exc:
        return _error(400, "bad_request", str(exc))
    ref = security.visitor_ref(g.visitor)
    scope = f"demo:{ref}"  # computed on the server; clients cannot name a scope
    rhash = _request_hash(op, cid, body)
    tid, expected = body["task_id"], body["expected_run_version"]
    try:
        with closing(_conn()) as conn:
            with db.write_transaction(conn):
                status, payload = _apply(conn, op, cid, tid, expected, body.get("action"), ref, scope, key, rhash)
    except sqlite3.IntegrityError:
        return _error(409, "version_conflict", "같은 시도가 동시에 바뀌었습니다. 새로고침 후 다시 시도하세요.")
    return jsonify(payload), status


def _apply(conn, op, cid, tid, expected, action, ref, scope, key, rhash):
    record, pkg, info = _company_state(conn, cid)
    if record is None or pkg is None:
        return 404, {"error": {"code": "not_found", "message": "공개 시연 회사를 찾을 수 없습니다."}}
    gate = info["gate"]
    if not gate["allowed"]:  # re-checked on every call, including retries
        return 409, {"error": {"code": "hold", "message": "현재 권리·검토 조건으로 시연이 보류되었습니다."},
                     "holds": [{"reason": h["reason"], "action": h["action"]} for h in gate["holds"]]}
    if key:
        stored = conn.execute("SELECT request_hash, response_json, http_status FROM demo_receipts "
                              "WHERE request_scope=? AND request_id=?", (scope, key)).fetchone()
        if stored is not None:
            if stored["request_hash"] != rhash:
                return 409, {"error": {"code": "idempotency_conflict",
                                       "message": "같은 요청 번호로 다른 내용이 이미 저장되었습니다."}}
            return stored["http_status"], {**json.loads(stored["response_json"]), "replayed": True}
    task = next((t for t in pkg["tasks"] if t["task_id"] == tid), None)
    if task is None:
        return 404, {"error": {"code": "not_found", "message": "회사에 없는 과제입니다."}}
    events = conn.execute("SELECT COUNT(*) AS n FROM demo_run_history WHERE demo_session_ref=?", (ref,)).fetchone()["n"]
    if events >= MAX_EVENTS_PER_VISITOR:
        return 429, {"error": {"code": "limit", "message": "이 세션의 시연 한도에 도달했습니다."}}

    row = _task_run(conn, cid, tid, ref)
    current = row["run_version"] if row is not None else 0
    if current != expected:
        return 409, {"error": {"code": "version_conflict",
                               "message": "실행 버전이 바뀌었습니다. 새로고침 후 다시 시도하세요."},
                     "run_version": current}
    now = db.now_iso()
    state = row["state"] if row is not None else "pending_review"
    inspected = bool(row["inspection_complete"]) if row is not None else False
    score = row["score"] if row is not None else None
    attempt = row["attempt"] if row is not None else 1
    detail = {}

    if op == "run":
        if state == "released":
            return 409, {"error": {"code": "released",
                                   "message": "이미 해제된 과제입니다. 초기화한 뒤 새 시도로 진행하세요."}}
        if action == "release" and not inspected:
            return 409, {"error": {"code": "inspection_required",
                                   "message": "검사 전 해제는 거절됩니다. 먼저 검사를 요청하세요."}}
        if action == "request_inspection":
            checks, score = _inspect(conn, pkg, task, gate["allowed"])
            new_state, inspected, detail = "inspected", True, {"checks": checks, "rule": "통과 항목 수 0~4"}
        elif action == "hold":
            new_state = "held"
        else:
            new_state, inspected = "released", True
        event, to_state = action, new_state
    else:
        new_state, inspected, score = "pending_review", False, None
        event, to_state = "reset", new_state

    version = current + 1
    if op == "reset" and row is not None:
        # keep the previous attempt as history, open a new one; run_version keeps growing
        cur = conn.execute("UPDATE demo_runs SET is_current=0 WHERE id=? AND run_version=?",
                           (row["id"], current))
        if cur.rowcount != 1:
            return 409, {"error": {"code": "version_conflict", "message": "다시 시도하세요."}}
        attempt += 1
        row = None
    if row is None:
        conn.execute(
            "INSERT INTO demo_runs (company_id, task_id, demo_session_ref, attempt, run_version, is_current, "
            "state, inspection_complete, score, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (cid, tid, ref, attempt, version, new_state, 1 if inspected else 0, score, now))
    else:
        cur = conn.execute(
            "UPDATE demo_runs SET state=?, inspection_complete=?, score=?, run_version=?, updated_at=? "
            "WHERE id=? AND run_version=?",
            (new_state, 1 if inspected else 0, score, version, now, row["id"], current))
        if cur.rowcount != 1:
            return 409, {"error": {"code": "version_conflict", "message": "다시 시도하세요."}}
    _record_event(conn, cid, tid, ref, attempt, version, event, state, to_state, score, detail, now)
    payload = {"run": {"attempt": attempt, "run_version": version, "state": new_state,
                       "inspection_complete": inspected, "score": score, "started": True}}
    if key:
        conn.execute(
            "INSERT INTO demo_receipts (request_scope, request_id, request_hash, response_json, http_status, "
            "created_at) VALUES (?, ?, ?, ?, 200, ?)", (scope, key, rhash, json.dumps(payload, sort_keys=True), now))
    return 200, payload


@bp.post("/companies/<cid>/run")
def run(cid):
    return _mutate(cid, "run")


@bp.post("/companies/<cid>/reset")
def reset(cid):
    return _mutate(cid, "reset")
