"""Buyer follow-up opinions (EPIC 10, F8.1~F8.4) and dashboard/trail helpers (F2.4, F2.5).

Feedback is stored as shared records (type "feedback"): title = topic/subject label,
body = opinion text, status = processing memo (null or 1~80 chars), fields = {kind, target_note}.
Follow-up targets are "about" links to materials/works/catalog/projects/companies.
No external messages are sent; saving never means a message, order or payment happened.
"""

import json
import sqlite3
from contextlib import closing

from flask import Blueprint, current_app, jsonify, request

from backend import db

bp = Blueprint("feedback", __name__, url_prefix="/api/feedback")

KIND_LABELS = {
    "sample_review": "샘플 검토",
    "pilot": "유료 파일럿 조건",
    "topic": "필요 주제",
}
TYPE_LABELS = {
    "materials": "자료", "experts": "전문가", "works": "작업(원시·정제)", "catalog": "카탈로그",
    "projects": "FDE 프로젝트", "companies": "가상회사", "rights": "권리", "feedback": "구매자 의견",
}
TYPE_STAGE = {
    "materials": "S1", "rights": "S2", "experts": "S3", "works": "S3·S5", "catalog": "S4",
    "projects": "S6", "companies": "S7", "feedback": "S0",
}
TYPE_PAGE = {
    "materials": "s1", "rights": "s2", "experts": "s3", "works": "s5", "catalog": "s4",
    "projects": "s6", "companies": "s7", "feedback": "dashboard",
}
# Follow-up targets a feedback record may be linked to (relation "about").
LINK_TYPES = ("materials", "works", "catalog", "projects", "companies")
LINK_RELATION = "about"
PENDING_STATUSES = {"draft", "pending", "pending_review", "review", "needs_review"}
HOLD_STATUSES = {"hold", "on_hold", "held", "blocked"}
MAX_JSON_BYTES = 1024 * 1024
MAX_TITLE = 120
MAX_NOTE = 2000
MAX_TARGET_NOTE = 500
MAX_STATUS = 80
CORE_KEYS = {"id", "type", "title", "status", "synthetic", "version", "material_hash",
             "input_hash", "body", "created_at", "updated_at"}
ALLOWED_CREATE = {"kind", "title", "note", "target_note", "status", "links", "synthetic"}
ALLOWED_PATCH = {"expected_version", "kind", "title", "note", "target_note", "status", "links"}


def _error(status, code, message, fields=None):
    body = {"code": code, "message": message}
    if fields:
        body["field_errors"] = fields
    return jsonify(error=body), status


def _conn():
    return db.connect(current_app.config["DB_PATH"])


def _json_body(allowed):
    """Return (data, None) or (None, error_response) per TRD §8.1 (object only, no unknown keys)."""
    if request.mimetype != "application/json":
        return None, _error(415, "unsupported_media_type", "application/json 으로 보내 주세요.")
    raw = request.get_data(cache=False)
    if len(raw) > MAX_JSON_BYTES:
        return None, _error(413, "payload_too_large", "요청이 1MiB를 넘었습니다.")

    def _reject(name):
        raise ValueError(name)

    def _no_dup(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(key)
            obj[key] = value
        return obj

    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_dup, parse_constant=_reject)
    except (UnicodeDecodeError, ValueError):
        data = None
    if not isinstance(data, dict):
        return None, _error(400, "invalid_json", "JSON 객체를 보내 주세요.")
    unknown = {k: "허용되지 않은 항목입니다." for k in data if k not in allowed}
    if unknown:
        return None, _error(400, "validation_error", "입력을 확인해 주세요.", unknown)
    return data, None


def _text(value, label, max_len, required, errors):
    if value is None:
        if required:
            errors[label] = "필수 항목입니다."
        return None
    if not isinstance(value, str):
        errors[label] = "문자열이어야 합니다."
        return None
    value = value.strip()
    if required and not value:
        errors[label] = "필수 항목입니다."
        return None
    if len(value) > max_len:
        errors[label] = f"{max_len}자 이하로 입력해 주세요."
        return None
    return value


def _validate_links(conn, raw_links, errors):
    """Return a list of (type, id) pairs that exist, or record field errors."""
    if raw_links is None:
        return None
    if not isinstance(raw_links, list) or len(raw_links) > 20:
        errors["links"] = "연결 목록은 20개 이하의 배열이어야 합니다."
        return None
    pairs = []
    for index, link in enumerate(raw_links):
        if not isinstance(link, dict) or set(link) != {"type", "id"}:
            errors["links"] = "연결은 {type, id} 형식이어야 합니다."
            return None
        ltype, lid = link["type"], link["id"]
        if ltype not in LINK_TYPES:
            errors["links"] = "연결할 수 없는 기록 유형입니다."
            return None
        if not isinstance(lid, str) or not lid or len(lid) > 64:
            errors["links"] = "기록 ID 형식이 올바르지 않습니다."
            return None
        if db.get_record(conn, ltype, lid) is None:
            errors["links"] = f"없는 기록입니다: {TYPE_LABELS[ltype]} {lid}"
            return None
        if (ltype, lid) not in pairs:
            pairs.append((ltype, lid))
    return pairs


def _about_links(conn, feedback_id):
    rows = conn.execute(
        "SELECT l.to_type, l.to_id, r.title, r.status, r.synthetic FROM record_links l "
        "JOIN records r ON r.type = l.to_type AND r.id = l.to_id "
        "WHERE l.from_type = 'feedback' AND l.from_id = ? AND l.relation = ? "
        "ORDER BY l.to_type, l.to_id",
        (feedback_id, LINK_RELATION),
    ).fetchall()
    return [_node(r["to_type"], r["to_id"], r["title"], r["status"], r["synthetic"], LINK_RELATION)
            for r in rows]


def _node(rtype, rid, title, status, synthetic, relation, direction=None):
    node = {
        "type": rtype, "id": rid, "title": title, "status": status,
        "synthetic": bool(synthetic), "type_label": TYPE_LABELS.get(rtype, rtype),
        "stage": TYPE_STAGE.get(rtype, ""), "page": TYPE_PAGE.get(rtype, ""),
        "relation": relation,
    }
    if direction:
        node["direction"] = direction
    return node


def _raw_fields(conn, feedback_id):
    row = conn.execute("SELECT fields_json FROM records WHERE type='feedback' AND id=?",
                       (feedback_id,)).fetchone()
    return json.loads(row["fields_json"]) if row else {}


def _feedback_item(conn, feedback_id):
    item = db.get_record(conn, "feedback", feedback_id)
    if item is None:
        return None
    fields = _raw_fields(conn, feedback_id)
    item["kind"] = fields.get("kind")
    item["kind_label"] = KIND_LABELS.get(fields.get("kind"), "")
    item["target_note"] = fields.get("target_note", "")
    item["note"] = item.get("body")
    return {"item": item, "links": _about_links(conn, feedback_id)}


def _next_action(kind_links, status):
    if kind_links == 0:
        return "연결 대상(자료·카탈로그·작업·FDE·회사)을 지정하세요."
    if status is None:
        return "처리 상태를 기록하세요."
    return "연결된 보완 기록을 확인하세요."


@bp.post("")
def create_feedback():
    data, err = _json_body(ALLOWED_CREATE)
    if err:
        return err
    errors = {}
    kind = data.get("kind")
    if kind not in KIND_LABELS:
        errors["kind"] = "샘플 검토·유료 파일럿 조건·필요 주제 중 하나를 선택하세요."
    title = _text(data.get("title"), "title", MAX_TITLE, True, errors)
    note = _text(data.get("note"), "note", MAX_NOTE, True, errors)
    target_note = _text(data.get("target_note"), "target_note", MAX_TARGET_NOTE, False, errors) or ""
    status = _text(data.get("status"), "status", MAX_STATUS, False, errors)
    if "synthetic" in data and data["synthetic"] is not True:
        errors["synthetic"] = "현재는 합성 의견만 저장할 수 있습니다(true)."
    try:
        idem = db.normalize_idempotency_key(request.headers.get("Idempotency-Key"))
    except ValueError as exc:
        return _error(400, "validation_error", str(exc), {"Idempotency-Key": str(exc)})
    try:
        with closing(_conn()) as conn:
            pairs = _validate_links(conn, data.get("links"), errors)
            if errors:
                return _error(400, "validation_error", "입력을 확인해 주세요.", errors)
            with db.write_transaction(conn):
                prior = db.replay_idempotent(conn, "feedback.create", idem, data) if idem else None
                if prior:
                    fid = prior[1]
                else:
                    fid = db.create_record(conn, "feedback", title=title, status=status or None,
                                           synthetic=True, body=note,
                                           fields={"kind": kind, "target_note": target_note})
                    for ltype, lid in pairs or []:
                        db.add_link(conn, "feedback", fid, ltype, lid, LINK_RELATION)
                    if idem:
                        db.remember_idempotent(conn, "feedback.create", idem, data, "feedback", fid)
            detail = _feedback_item(conn, fid)
    except db.IdempotencyConflict:
        return _error(409, "idempotency_conflict",
                      "같은 요청 번호로 다른 내용이 이미 저장되었습니다. 새로 작성해 주세요.")
    except sqlite3.Error:
        return _error(500, "storage_error", "저장하지 못했습니다. 입력은 그대로 두었으니 다시 시도해 주세요.")
    return jsonify(detail), 201


@bp.get("")
def list_feedback():
    kind = request.args.get("kind")
    if kind is not None and kind not in KIND_LABELS:
        return _error(400, "validation_error", "알 수 없는 의견 유형입니다.", {"kind": "유형 오류"})
    items = []
    with closing(_conn()) as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM records WHERE type='feedback' ORDER BY created_at DESC, id DESC")]
        for fid in ids:
            detail = _feedback_item(conn, fid)
            if detail is None or (kind and detail["item"]["kind"] != kind):
                continue
            items.append(detail["item"] | {"link_count": len(detail["links"])})
    return jsonify(items=items, total=len(items))


@bp.get("/<feedback_id>")
def get_feedback(feedback_id):
    with closing(_conn()) as conn:
        detail = _feedback_item(conn, feedback_id)
        if detail is None:
            return _error(404, "not_found", "의견을 찾을 수 없습니다.")
        _, missing = db.relations(conn, "feedback", feedback_id)
    return jsonify(detail | {"missing_links": missing})


@bp.patch("/<feedback_id>")
def update_feedback(feedback_id):
    data, err = _json_body(ALLOWED_PATCH)
    if err:
        return err
    errors = {}
    expected = data.get("expected_version")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        errors["expected_version"] = "현재 버전 번호가 필요합니다."
    changes = {}
    fields_changes = {}
    if "kind" in data:
        if data["kind"] not in KIND_LABELS:
            errors["kind"] = "샘플 검토·유료 파일럿 조건·필요 주제 중 하나를 선택하세요."
        else:
            fields_changes["kind"] = data["kind"]
    if "title" in data:
        changes["title"] = _text(data["title"], "title", MAX_TITLE, True, errors)
    if "note" in data:
        changes["body"] = _text(data["note"], "note", MAX_NOTE, True, errors)
    if "target_note" in data:
        fields_changes["target_note"] = _text(data["target_note"], "target_note",
                                              MAX_TARGET_NOTE, False, errors) or ""
    if "status" in data:
        status = _text(data["status"], "status", MAX_STATUS, False, errors)
        changes["status"] = status or None
    try:
        with closing(_conn()) as conn:
            current = db.get_record(conn, "feedback", feedback_id)
            if current is None:
                return _error(404, "not_found", "의견을 찾을 수 없습니다.")
            pairs = _validate_links(conn, data["links"], errors) if "links" in data else None
            if errors:
                return _error(400, "validation_error", "입력을 확인해 주세요.", errors)
            if current["version"] != expected:
                return _error(409, "version_conflict",
                              "다른 곳에서 먼저 저장되었습니다. 다시 열어 확인해 주세요.")
            with db.write_transaction(conn):
                if fields_changes or changes:
                    merged = {**_raw_fields(conn, feedback_id), **fields_changes}
                    if fields_changes:
                        changes["fields"] = merged
                    if changes:
                        db.update_record(conn, "feedback", feedback_id,
                                         expected_version=expected, changes=changes)
                if pairs is not None:
                    conn.execute(
                        "DELETE FROM record_links WHERE from_type='feedback' AND from_id=? "
                        "AND relation=?", (feedback_id, LINK_RELATION))
                    for ltype, lid in pairs:
                        db.add_link(conn, "feedback", feedback_id, ltype, lid, LINK_RELATION)
            detail = _feedback_item(conn, feedback_id)
    except db.RecordConflict:
        return _error(409, "version_conflict", "다른 곳에서 먼저 저장되었습니다. 다시 열어 확인해 주세요.")
    except db.RecordNotFound:
        return _error(404, "not_found", "의견을 찾을 수 없습니다.")
    except sqlite3.Error:
        return _error(500, "storage_error", "저장하지 못했습니다. 입력은 그대로 두었으니 다시 시도해 주세요.")
    return jsonify(detail)


@bp.get("/trail/<record_type>/<record_id>")
def trail(record_type, record_id):
    """Linked records in both directions, so a screen can follow relations to their stage."""
    if record_type not in db.RECORD_TYPES:
        return _error(404, "unknown_record_type", "알 수 없는 기록 유형입니다.")
    with closing(_conn()) as conn:
        item = db.get_record(conn, record_type, record_id)
        if item is None:
            return _error(404, "not_found", "기록을 찾을 수 없습니다.")
        out_rows = conn.execute(
            "SELECT l.relation, r.type, r.id, r.title, r.status, r.synthetic FROM record_links l "
            "JOIN records r ON r.type = l.to_type AND r.id = l.to_id "
            "WHERE l.from_type = ? AND l.from_id = ? ORDER BY l.relation, r.type, r.id",
            (record_type, record_id)).fetchall()
        in_rows = conn.execute(
            "SELECT l.relation, r.type, r.id, r.title, r.status, r.synthetic FROM record_links l "
            "JOIN records r ON r.type = l.from_type AND r.id = l.from_id "
            "WHERE l.to_type = ? AND l.to_id = ? ORDER BY l.relation, r.type, r.id",
            (record_type, record_id)).fetchall()
        _, missing = db.relations(conn, record_type, record_id)
    outgoing = [_node(r["type"], r["id"], r["title"], r["status"], r["synthetic"], r["relation"], "out")
                for r in out_rows]
    incoming = [_node(r["type"], r["id"], r["title"], r["status"], r["synthetic"], r["relation"], "in")
                for r in in_rows]
    return jsonify(item=_node(record_type, record_id, item["title"], item["status"],
                              item["synthetic"], None),
                   outgoing=outgoing, incoming=incoming, missing_links=missing)


@bp.get("/dashboard")
def dashboard():
    """S0 read model: stage counts, pending reviews, holds, recent activity and feedback next actions."""
    with closing(_conn()) as conn:
        counts = db.counts(conn)
        stages = [
            {"type": t, "label": TYPE_LABELS[t], "stage": TYPE_STAGE[t], "page": TYPE_PAGE[t],
             "count": counts.get(t, 0)}
            for t in ("materials", "rights", "experts", "works", "catalog", "projects", "companies",
                      "feedback")
        ]
        rows = conn.execute(
            "SELECT type, id, title, status, synthetic, version, updated_at FROM records "
            "ORDER BY updated_at DESC, id DESC LIMIT 200").fetchall()
        recent = [_node(r["type"], r["id"], r["title"], r["status"], r["synthetic"], None)
                  | {"version": r["version"], "updated_at": r["updated_at"]}
                  for r in rows[:10]]
        pending = [_node(r["type"], r["id"], r["title"], r["status"], r["synthetic"], None)
                   for r in rows if r["type"] != "feedback" and r["status"] in PENDING_STATUSES]
        holds = [_node(r["type"], r["id"], r["title"], r["status"], r["synthetic"], None)
                 for r in rows if r["type"] != "feedback" and r["status"] in HOLD_STATUSES]
        feedback = []
        kind_counts = {k: 0 for k in KIND_LABELS}
        for fid in [r["id"] for r in conn.execute(
                "SELECT id FROM records WHERE type='feedback' ORDER BY created_at DESC, id DESC LIMIT 50")]:
            detail = _feedback_item(conn, fid)
            if detail is None:
                continue
            item = detail["item"]
            kind_counts[item["kind"]] = kind_counts.get(item["kind"], 0) + 1
            feedback.append({
                "id": item["id"], "title": item["title"], "kind": item["kind"],
                "kind_label": item["kind_label"], "status": item["status"],
                "synthetic": item["synthetic"], "version": item["version"],
                "link_count": len(detail["links"]),
                "next_action": _next_action(len(detail["links"]), item["status"]),
            })
    return jsonify(stages=stages, recent_activity=recent, pending_reviews=pending[:20],
                   pending_total=len(pending), holds=holds[:20], hold_total=len(holds),
                   feedback=feedback, feedback_kind_counts=kind_counts,
                   status_rule={"pending": sorted(PENDING_STATUSES), "hold": sorted(HOLD_STATUSES)})
