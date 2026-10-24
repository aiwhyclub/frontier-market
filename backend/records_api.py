"""Flask blueprint for the record store (F2.1). Common format follows TRD §8.1.

Responses: list {"items": [...], "total": n}; detail {"item", "links", "missing_links"};
summary {"counts": {...}}. Errors: {"error": {"code", "message"[, "fields"]}}.
"""

import json
import sqlite3
from contextlib import closing

from flask import Blueprint, current_app, jsonify, request

from backend import db

bp = Blueprint("records", __name__, url_prefix="/api/records")

MAX_JSON_BYTES = 1024 * 1024
MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 20000
MATERIAL_KEYS = {"title", "body", "synthetic"}
MAX_QUERY_CHARS = 100


def _error(status, code, message, **extra):
    return jsonify(error={"code": code, "message": message, **extra}), status


def _conn():
    return db.connect(current_app.config["DB_PATH"])


def _reject_constant(name):
    raise ValueError(f"constant not allowed: {name}")


def _no_duplicate_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key: {key}")
        obj[key] = value
    return obj


def _parse_json_object(raw):
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys,
                          parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _int_arg(name, default, low, high):
    raw = request.args.get(name)
    if raw is None:
        return default, None
    try:
        value = int(raw)
    except ValueError:
        return None, f"{name}: 정수여야 합니다."
    if not low <= value <= high:
        return None, f"{name}: {low}~{high} 사이여야 합니다."
    return value, None


def _text_arg(name, max_chars):
    """Optional text filter. Blank values mean "no filter"; the value is trimmed."""
    raw = request.args.get(name)
    if raw is None:
        return "", None
    value = raw.strip()
    if len(value) > max_chars:
        return None, f"{name}: {max_chars}자 이하여야 합니다."
    return value, None


def _like_escape(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_records(conn, record_type, *, q="", field="", status="", limit=50, offset=0):
    """List one record type with optional filters (TRD §8.1).

    q: case-insensitive substring of title or body (ASCII case only; Hangul matches as-is).
    field: exact match on the record's 분야 value (fields_json "domain").
    status: exact match on the record status.
    Without filters the result equals db.list_records. Order is created_at DESC, id DESC.
    """
    db.check_type(record_type)
    clauses = ["type = ?"]
    params = [record_type]
    if q:
        pattern = f"%{_like_escape(q)}%"
        clauses.append("(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')")
        params += [pattern, pattern]
    if field:
        clauses.append("json_extract(fields_json, '$.domain') = ?")
        params.append(field)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = " AND ".join(clauses)
    total = conn.execute(f"SELECT COUNT(*) AS n FROM records WHERE {where}", params).fetchone()["n"]
    rows = conn.execute(
        "SELECT id, type, title, status, synthetic, version, updated_at FROM records "
        f"WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    items = [
        {
            "id": r["id"],
            "type": r["type"],
            "title": r["title"],
            "status": r["status"],
            "synthetic": bool(r["synthetic"]),
            "version": r["version"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]
    return items, total


def _validate_material(data):
    errors = {}
    for key in data:
        if key not in MATERIAL_KEYS:
            errors[key] = "허용되지 않은 항목입니다."
    title = data.get("title")
    if "title" not in data:
        errors["title"] = "필수 항목입니다."
    elif not isinstance(title, str) or not 1 <= len(title.strip()) <= MAX_TITLE_CHARS:
        errors["title"] = f"제목은 1~{MAX_TITLE_CHARS}자 문자열이어야 합니다."
    body = data.get("body")
    if "body" not in data:
        errors["body"] = "필수 항목입니다."
    elif not isinstance(body, str) or not 1 <= len(body.strip()) <= MAX_BODY_CHARS:
        errors["body"] = f"본문은 1~{MAX_BODY_CHARS}자 문자열이어야 합니다."
    if "synthetic" not in data:
        errors["synthetic"] = "필수 항목입니다."
    elif data["synthetic"] is not True:
        errors["synthetic"] = "현재는 합성 자료만 등록할 수 있습니다(true)."
    return errors


@bp.get("/summary")
def summary():
    with closing(_conn()) as conn:
        return jsonify(counts=db.counts(conn))


@bp.get("/<record_type>")
def list_records(record_type):
    if record_type not in db.RECORD_TYPES:
        return _error(404, "unknown_record_type", "알 수 없는 기록 유형입니다.")
    limit, err = _int_arg("limit", 50, 1, 100)
    if err:
        return _error(400, "validation_error", err, fields={"limit": err})
    offset, err = _int_arg("offset", 0, 0, 10**9)
    if err:
        return _error(400, "validation_error", err, fields={"offset": err})
    q, err = _text_arg("q", MAX_QUERY_CHARS)
    if err:
        return _error(400, "validation_error", err, fields={"q": err})
    field, err = _text_arg("field", MAX_QUERY_CHARS)
    if err:
        return _error(400, "validation_error", err, fields={"field": err})
    status, err = _text_arg("status", MAX_QUERY_CHARS)
    if err:
        return _error(400, "validation_error", err, fields={"status": err})
    with closing(_conn()) as conn:
        items, total = _search_records(conn, record_type, q=q, field=field, status=status,
                                       limit=limit, offset=offset)
    return jsonify(items=items, total=total)


@bp.get("/<record_type>/<record_id>")
def get_record(record_type, record_id):
    if record_type not in db.RECORD_TYPES:
        return _error(404, "unknown_record_type", "알 수 없는 기록 유형입니다.")
    with closing(_conn()) as conn:
        item = db.get_record(conn, record_type, record_id)
        if item is None:
            return _error(404, "not_found", "기록을 찾을 수 없습니다.")
        links, missing = db.relations(conn, record_type, record_id)
    return jsonify(item=item, links=links, missing_links=missing)


@bp.post("/materials")
def create_material():
    if request.mimetype != "application/json":
        return _error(415, "unsupported_media_type", "application/json 으로 보내 주세요.")
    if request.content_length and request.content_length > MAX_JSON_BYTES:
        return _error(413, "payload_too_large", "요청이 1MiB를 넘었습니다.")
    raw = request.get_data(cache=False)
    if len(raw) > MAX_JSON_BYTES:
        return _error(413, "payload_too_large", "요청이 1MiB를 넘었습니다.")
    data = _parse_json_object(raw)
    if data is None:
        return _error(400, "invalid_json", "JSON 객체를 보내 주세요.")
    errors = _validate_material(data)
    if errors:
        return _error(400, "validation_error", "입력을 확인해 주세요.", fields=errors)

    title = data["title"].strip()
    body = data["body"].strip()
    try:
        with closing(_conn()) as conn:
            with db.write_transaction(conn):
                rid = db.create_record(conn, "materials", title=title, status="draft",
                                       synthetic=True, body=body)
                item = db.get_record(conn, "materials", rid)
    except sqlite3.Error:
        return _error(500, "storage_error", "저장하지 못했습니다. 입력은 그대로 두었으니 다시 시도해 주세요.")
    return jsonify(item=item), 201
