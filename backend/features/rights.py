"""F9.1 / F9.3 rights (이용 범위) records, gate state and history (STORY 6.1-6.2).

Rights are stored as `rights` records in the shared record store (type "rights").
A grant is bound to one subject revision's material_hash H (fixed by the server from
record_revisions). Status-only edits of the subject keep H, so the grant still applies.
Gate rule (conservative): a subject is "allowed" only when every applicable grant (same H)
is confirmed/restricted, not expired, and at least one covers the requested purpose.
Anything else is "hold" with reasons. Each write also appends a row to rights_events.
"""

import json
import sqlite3
import re
import secrets
from contextlib import closing
from datetime import date

from flask import Blueprint, current_app, jsonify, request

from backend import db

bp = Blueprint("rights", __name__, url_prefix="/api/rights")

SUBJECT_TYPES = ("materials", "experts", "works")
PURPOSES = ("training", "evaluation")
LEGACY_PURPOSE_MAP = {"learning": "training"}  # seed rows used "learning"
PERMISSIONS = ("unknown", "allowed", "denied")
STATUSES = ("confirmed", "restricted", "unconfirmed", "revoked")
USABLE_STATUSES = ("confirmed", "restricted")
MAX_TEXT = 1000
MAX_NAME = 120
MAX_ACTOR = 60
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FIELD_KEYS = (
    "subject_type", "subject_id", "subject_version", "subject_material_hash", "holder",
    "purposes", "derivative", "distribution", "reason", "evidence", "restrictions",
    "valid_until", "confirmed_by", "confirmed_at", "recorded_by", "recorded_at",
)

SCHEMA = """CREATE TABLE IF NOT EXISTS rights_events (
    id TEXT PRIMARY KEY,
    rights_id TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    subject_version INTEGER,
    subject_material_hash TEXT,
    action TEXT NOT NULL,
    status_from TEXT,
    status_to TEXT,
    reason TEXT NOT NULL,
    evidence TEXT,
    actor TEXT NOT NULL,
    rights_version INTEGER,
    at TEXT NOT NULL
)"""


def init_schema(conn):
    conn.execute(SCHEMA)
    conn.execute("CREATE INDEX IF NOT EXISTS rights_events_subject ON rights_events (subject_type, subject_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS rights_events_rights ON rights_events (rights_id)")


def _error(status, code, message, **extra):
    return jsonify(error={"code": code, "message": message, **extra}), status


def _conn():
    conn = db.connect(current_app.config["DB_PATH"])
    init_schema(conn)
    return conn


def _today():
    return date.today().isoformat()


def _text(value, limit):
    if value is None:
        return None
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value:
        return None
    if len(value) > limit:
        return False
    return value


def _clean(merged):
    """Validate the merged payload. Returns (clean_dict, errors)."""
    errors = {}
    clean = {}

    stype = merged.get("subject_type")
    if stype not in SUBJECT_TYPES:
        errors["subject_type"] = "materials, experts, works 중 하나여야 합니다."
    clean["subject_type"] = stype

    sid = _text(merged.get("subject_id"), 80)
    if not sid:
        errors["subject_id"] = "대상 ID를 입력해 주세요."
    clean["subject_id"] = sid

    try:
        sver = int(str(merged.get("subject_version")).strip())
        if sver < 1:
            raise ValueError
        clean["subject_version"] = sver
    except (TypeError, ValueError):
        errors["subject_version"] = "대상 개정 번호는 1 이상의 정수여야 합니다."

    holder = _text(merged.get("holder"), MAX_NAME)
    if not holder:
        errors["holder"] = "권리자를 입력해 주세요(120자 이내)."
    clean["holder"] = holder

    raw_purposes = merged.get("purposes")
    if isinstance(raw_purposes, str):
        raw_purposes = [p for p in re.split(r"[,\s]+", raw_purposes) if p]
    purposes = []
    if not isinstance(raw_purposes, list):
        errors["purposes"] = "목적을 선택해 주세요(training, evaluation)."
    else:
        for p in raw_purposes:
            p = LEGACY_PURPOSE_MAP.get(p, p)
            if p not in PURPOSES:
                errors["purposes"] = "목적은 training 또는 evaluation만 가능합니다."
                break
            if p not in purposes:
                purposes.append(p)
        if not purposes and "purposes" not in errors:
            errors["purposes"] = "목적을 하나 이상 선택해 주세요."
    clean["purposes"] = sorted(purposes)

    for key in ("derivative", "distribution"):
        value = merged.get(key)
        if value not in PERMISSIONS:
            errors[key] = "unknown, allowed, denied 중 하나여야 합니다."
        clean[key] = value

    status = merged.get("status")
    if status not in STATUSES:
        errors["status"] = "confirmed, restricted, unconfirmed, revoked 중 하나여야 합니다."
    clean["status"] = status

    reason = _text(merged.get("reason"), MAX_TEXT)
    if not reason:
        errors["reason"] = "사유를 입력해 주세요."
    clean["reason"] = reason

    evidence = _text(merged.get("evidence"), MAX_TEXT)
    if evidence is False:
        errors["evidence"] = f"근거는 {MAX_TEXT}자 이내 문자열이어야 합니다."
    if status in USABLE_STATUSES and not evidence:
        errors["evidence"] = "확인·제한 상태에는 근거가 필요합니다."
    clean["evidence"] = evidence or None

    restrictions = _text(merged.get("restrictions"), MAX_TEXT)
    if restrictions is False:
        errors["restrictions"] = f"제한은 {MAX_TEXT}자 이내 문자열이어야 합니다."
    if status == "restricted" and not restrictions:
        errors["restrictions"] = "제한 확인에는 제한 내용을 입력해 주세요."
    clean["restrictions"] = restrictions or None

    valid_until = merged.get("valid_until") or None
    if valid_until is not None:
        if not isinstance(valid_until, str) or not DATE_RE.match(valid_until):
            errors["valid_until"] = "기간 종료일은 YYYY-MM-DD 형식이어야 합니다."
        else:
            try:
                date.fromisoformat(valid_until)
            except ValueError:
                errors["valid_until"] = "존재하는 날짜여야 합니다."
            else:
                if status in USABLE_STATUSES and valid_until < _today():
                    errors["valid_until"] = "기간 종료일이 지났습니다. 새 기간으로 다시 확인해 주세요."
    clean["valid_until"] = valid_until

    actor = _text(merged.get("actor"), MAX_ACTOR)
    if not actor:
        errors["actor"] = "담당자(지정 운영책임자)를 입력해 주세요."
    clean["actor"] = actor

    return clean, errors


def _resolve_subject(conn, stype, sid, sver):
    """Return (subject_item, revision_hash, error_message). Fixes H from the revision row."""
    subject = db.get_record(conn, stype, sid)
    if subject is None:
        return None, None, "대상 기록을 찾을 수 없습니다."
    rev = conn.execute(
        "SELECT material_hash FROM record_revisions WHERE type = ? AND id = ? AND version = ?",
        (stype, sid, sver),
    ).fetchone()
    if rev is None:
        return subject, None, "대상 개정 번호를 찾을 수 없습니다."
    return subject, rev["material_hash"], None


def _rights_ids_for(conn, stype, sid):
    rows = conn.execute(
        "SELECT id FROM records WHERE type = 'rights' "
        "AND json_extract(fields_json, '$.subject_type') = ? "
        "AND json_extract(fields_json, '$.subject_id') = ? ORDER BY id",
        (stype, sid),
    ).fetchall()
    return [r["id"] for r in rows]


def _effective(item, today):
    status = item.get("status")
    if status in USABLE_STATUSES and item.get("valid_until") and item["valid_until"] < today:
        return "expired"
    if status in STATUSES:
        return status
    return "legacy"


def _summary(item, today, current_hash):
    eff = _effective(item, today)
    return {
        "id": item["id"],
        "title": item["title"],
        "status": item.get("status"),
        "effective": eff,
        "synthetic": item["synthetic"],
        "version": item["version"],
        "subject_type": item.get("subject_type"),
        "subject_id": item.get("subject_id"),
        "subject_version": item.get("subject_version"),
        "subject_material_hash": item.get("subject_material_hash"),
        "applies_now": bool(current_hash) and item.get("subject_material_hash") == current_hash,
        "purposes": item.get("purposes") or [],
        "derivative": item.get("derivative"),
        "distribution": item.get("distribution"),
        "holder": item.get("holder"),
        "valid_until": item.get("valid_until"),
        "restrictions": item.get("restrictions"),
        "evidence": item.get("evidence"),
        "reason": item.get("reason"),
        "confirmed_by": item.get("confirmed_by"),
        "confirmed_at": item.get("confirmed_at"),
        "recorded_by": item.get("recorded_by"),
        "recorded_at": item.get("recorded_at"),
        "updated_at": item["updated_at"],
    }


def gate_state(conn, stype, sid, purpose=None):
    """Current use state for a subject. Returns None when the subject does not exist."""
    subject = db.get_record(conn, stype, sid)
    if subject is None:
        return None
    current_hash = subject["material_hash"]
    today = _today()
    applicable, past, legacy = [], 0, 0
    for rid in _rights_ids_for(conn, stype, sid):
        item = db.get_record(conn, "rights", rid)
        if item.get("subject_material_hash") is None:
            legacy += 1  # not bound to a revision H: cannot be used for the current content
            continue
        if item["subject_material_hash"] == current_hash:
            applicable.append(_summary(item, today, current_hash))
        else:
            past += 1

    reasons = []
    usable = []
    if not applicable:
        reasons.append("현재 내용(H)에 대한 권리 확인 기록이 없습니다.")
    for g in applicable:
        if g["effective"] == "unconfirmed":
            reasons.append(f"'{g['title']}': 권리 미확인 상태입니다.")
        elif g["effective"] == "revoked":
            reasons.append(f"'{g['title']}': 권리가 철회됐습니다.")
        elif g["effective"] == "expired":
            reasons.append(f"'{g['title']}': 이용 기간이 끝났습니다.")
        elif g["effective"] == "legacy":
            reasons.append(f"'{g['title']}': 이전 형식 기록이라 현재 내용에 고정되지 않았습니다.")
        else:
            usable.append(g)
    if purpose is not None:
        if purpose not in PURPOSES:
            raise ValueError("purpose")
        if not any(purpose in g["purposes"] for g in usable):
            reasons.append(f"목적 '{purpose}' 이용이 확인되지 않았습니다.")
    state = "hold" if reasons or not usable else "allowed"
    return {
        "state": state,
        "purpose": purpose,
        "subject": {
            "type": stype, "id": sid, "title": subject["title"],
            "version": subject["version"], "material_hash": current_hash,
            "synthetic": subject["synthetic"],
        },
        "reasons": reasons,
        "grants": applicable,
        "usable_grant_ids": [g["id"] for g in usable],
        "past_grant_count": past,
        "unbound_grant_count": legacy,
    }


def _event(conn, rights_id, fields, action, status_from, status_to, reason, evidence, actor,
           rights_version):
    conn.execute(
        "INSERT INTO rights_events (id, rights_id, subject_type, subject_id, subject_version, "
        "subject_material_hash, action, status_from, status_to, reason, evidence, actor, "
        "rights_version, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"rev_{secrets.token_hex(6)}", rights_id, fields["subject_type"], fields["subject_id"],
         fields["subject_version"], fields["subject_material_hash"], action, status_from,
         status_to, reason, evidence, actor, rights_version, db.now_iso()),
    )


def _fields_from(clean, subject_hash, previous):
    """Build stored fields. confirmed_* changes only when the status enters confirmed/restricted."""
    fields = {k: clean[k] for k in ("subject_type", "subject_id", "subject_version", "holder",
                                    "purposes", "derivative", "distribution", "reason",
                                    "evidence", "restrictions", "valid_until")}
    fields["subject_material_hash"] = subject_hash
    fields["recorded_by"] = clean["actor"]
    fields["recorded_at"] = db.now_iso()
    prev_status = (previous or {}).get("status")
    if clean["status"] in USABLE_STATUSES and (prev_status not in USABLE_STATUSES or previous is None
                                              or previous.get("subject_material_hash") != subject_hash):
        fields["confirmed_by"] = clean["actor"]
        fields["confirmed_at"] = db.now_iso()
    else:
        fields["confirmed_by"] = (previous or {}).get("confirmed_by")
        fields["confirmed_at"] = (previous or {}).get("confirmed_at")
    return fields


def _title(subject, sver):
    return f"권리: {subject['title']} (개정 {sver})"[:MAX_NAME]


def _body_json():
    if request.mimetype != "application/json":
        return None, _error(415, "unsupported_media_type", "application/json 으로 보내 주세요.")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, _error(400, "invalid_json", "JSON 객체를 보내 주세요.")
    return data, None


@bp.get("")
def list_rights():
    stype = request.args.get("subject_type")
    sid = request.args.get("subject_id")
    if stype is not None and stype not in SUBJECT_TYPES:
        return _error(400, "validation_error", "subject_type이 올바르지 않습니다.")
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 100)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        return _error(400, "validation_error", "limit/offset은 정수여야 합니다.")
    today = _today()
    with closing(_conn()) as conn:
        where = ["type = 'rights'"]
        params = []
        if stype:
            where.append("json_extract(fields_json, '$.subject_type') = ?")
            params.append(stype)
        if sid:
            where.append("json_extract(fields_json, '$.subject_id') = ?")
            params.append(sid)
        clause = " AND ".join(where)
        total = conn.execute(f"SELECT COUNT(*) AS n FROM records WHERE {clause}", params).fetchone()["n"]
        ids = conn.execute(
            f"SELECT id FROM records WHERE {clause} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        items = []
        for row in ids:
            item = db.get_record(conn, "rights", row["id"])
            subject = db.get_record(conn, item.get("subject_type"), item.get("subject_id")) \
                if item.get("subject_type") in SUBJECT_TYPES else None
            current = subject["material_hash"] if subject else None
            items.append(_summary(item, today, current))
    return jsonify(items=items, total=total)


@bp.get("/state")
def rights_state():
    stype = request.args.get("subject_type")
    sid = request.args.get("subject_id")
    purpose = request.args.get("purpose") or None
    if stype not in SUBJECT_TYPES or not sid:
        return _error(400, "validation_error", "subject_type과 subject_id가 필요합니다.")
    if purpose is not None and purpose not in PURPOSES:
        return _error(400, "validation_error", "purpose는 training 또는 evaluation입니다.")
    with closing(_conn()) as conn:
        state = gate_state(conn, stype, sid, purpose)
    if state is None:
        return _error(404, "not_found", "대상 기록을 찾을 수 없습니다.")
    return jsonify(state)


@bp.post("")
def create_rights():
    data, err = _body_json()
    if err:
        return err
    with closing(_conn()) as conn:
        if data.get("subject_type") not in SUBJECT_TYPES:
            return _error(400, "validation_error", "입력을 확인해 주세요.",
                          fields={"subject_type": "materials, experts, works 중 하나여야 합니다."})
        clean, errors = _clean(data)
        if errors:
            return _error(400, "validation_error", "입력을 확인해 주세요.", fields=errors)
        subject, subject_hash, msg = _resolve_subject(
            conn, clean["subject_type"], clean["subject_id"], clean["subject_version"])
        if subject is None:
            return _error(404, "not_found", msg)
        if msg:
            return _error(400, "validation_error", "입력을 확인해 주세요.",
                          fields={"subject_version": msg})
        try:
            with db.write_transaction(conn):
                fields = _fields_from(clean, subject_hash, None)
                rid = db.create_record(
                    conn, "rights", title=_title(subject, clean["subject_version"]),
                    status=clean["status"], synthetic=bool(subject["synthetic"]), fields=fields)
                db.add_link(conn, "rights", rid, clean["subject_type"], clean["subject_id"], "subject")
                _event(conn, rid, fields, "create", None, clean["status"], clean["reason"],
                       clean["evidence"], clean["actor"], 1)
                item = db.get_record(conn, "rights", rid)
        except sqlite3.Error:
            return _error(500, "storage_error", "저장하지 못했습니다. 입력은 그대로 두었으니 다시 시도해 주세요.")
        state = gate_state(conn, clean["subject_type"], clean["subject_id"])
    return jsonify(item=_summary(item, _today(), state["subject"]["material_hash"]), state=state), 201


@bp.get("/<rights_id>")
def get_rights(rights_id):
    with closing(_conn()) as conn:
        item = db.get_record(conn, "rights", rights_id)
        if item is None:
            return _error(404, "not_found", "권리 기록을 찾을 수 없습니다.")
        subject = db.get_record(conn, item["subject_type"], item["subject_id"]) \
            if item.get("subject_type") in SUBJECT_TYPES else None
        revisions = _revisions(conn, "rights", rights_id)
        events = _events_for_rights(conn, rights_id)
        summary = _summary(item, _today(), subject["material_hash"] if subject else None)
    return jsonify(item=summary, revisions=revisions, events=events,
                   subject={"type": item.get("subject_type"), "id": item.get("subject_id"),
                            "title": subject["title"] if subject else None,
                            "version": subject["version"] if subject else None})


@bp.patch("/<rights_id>")
def patch_rights(rights_id):
    data, err = _body_json()
    if err:
        return err
    expected = data.get("expected_version")
    if not isinstance(expected, int) or isinstance(expected, bool):
        return _error(400, "validation_error", "expected_version(정수)이 필요합니다.",
                      fields={"expected_version": "필수 항목입니다."})
    if not _text(data.get("reason"), MAX_TEXT) or not _text(data.get("actor"), MAX_ACTOR):
        return _error(400, "validation_error", "변경 사유와 담당자를 입력해 주세요.",
                      fields={"reason": "사유를 입력해 주세요.", "actor": "담당자를 입력해 주세요."})
    with closing(_conn()) as conn:
        current = db.get_record(conn, "rights", rights_id)
        if current is None:
            return _error(404, "not_found", "권리 기록을 찾을 수 없습니다.")
        if current["version"] != expected:
            return _error(409, "version_conflict",
                          "다른 곳에서 먼저 바뀌었어요. 다시 읽고 비교한 뒤 저장해 주세요.",
                          current_version=current["version"])
        for key in ("subject_type", "subject_id"):
            if key in data and data[key] != current.get(key):
                return _error(400, "validation_error", "대상은 바꿀 수 없습니다. 새 권리 기록을 만드세요.",
                              fields={key: "변경할 수 없습니다."})
        base = {k: current.get(k) for k in ("subject_type", "subject_id", "subject_version", "holder",
                                            "purposes", "derivative", "distribution", "evidence",
                                            "restrictions", "valid_until", "status")}
        merged = {**base, **{k: v for k, v in data.items() if k in base or k in ("reason", "actor")}}
        merged.pop("expected_version", None)
        clean, errors = _clean(merged)
        if errors:
            return _error(400, "validation_error", "입력을 확인해 주세요.", fields=errors)
        subject, subject_hash, msg = _resolve_subject(
            conn, clean["subject_type"], clean["subject_id"], clean["subject_version"])
        if subject is None:
            return _error(404, "not_found", msg)
        if msg:
            return _error(400, "validation_error", "입력을 확인해 주세요.",
                          fields={"subject_version": msg})
        status_from = current.get("status")
        fields = _fields_from(clean, subject_hash, current)
        substantive = ("subject_version", "subject_material_hash", "holder", "purposes", "derivative",
                       "distribution", "reason", "evidence", "restrictions", "valid_until")
        if clean["status"] == status_from and all(fields.get(k) == current.get(k) for k in substantive):
            # identical reconfirmation: no new version, event or confirmation timestamp
            state = gate_state(conn, clean["subject_type"], clean["subject_id"])
            return jsonify(item=_summary(current, _today(), state["subject"]["material_hash"]), state=state)
        try:
            with db.write_transaction(conn):
                changes = {"status": clean["status"], "fields": fields,
                           "title": _title(subject, clean["subject_version"])}
                item = db.update_record(conn, "rights", rights_id, expected_version=expected,
                                        changes=changes)
                if item["version"] != current["version"]:
                    action = "status_change" if status_from != clean["status"] else "update"
                    _event(conn, rights_id, fields, action, status_from, clean["status"],
                           clean["reason"], clean["evidence"], clean["actor"], item["version"])
        except db.RecordConflict:
            return _error(409, "version_conflict", "다른 곳에서 먼저 바뀌었어요. 다시 읽어 주세요.")
        except sqlite3.Error:
            return _error(500, "storage_error", "저장하지 못했습니다. 입력은 그대로 두었으니 다시 시도해 주세요.")
        state = gate_state(conn, clean["subject_type"], clean["subject_id"])
    return jsonify(item=_summary(item, _today(), state["subject"]["material_hash"]), state=state)


def _revisions(conn, record_type, record_id):
    current = db.get_record(conn, record_type, record_id)
    rows = conn.execute(
        "SELECT version, material_hash, snapshot_json, created_at FROM record_revisions "
        "WHERE type = ? AND id = ? ORDER BY version DESC",
        (record_type, record_id),
    ).fetchall()
    return [
        {
            "version": r["version"],
            "material_hash": r["material_hash"],
            "created_at": r["created_at"],
            "is_current": current is not None and r["version"] == current["version"],
            "snapshot": json.loads(r["snapshot_json"]),
        }
        for r in rows
    ]


def _events_for_rights(conn, rights_id):
    rows = conn.execute(
        "SELECT * FROM rights_events WHERE rights_id = ? ORDER BY at, id", (rights_id,)
    ).fetchall()
    return [dict(r) for r in rows]


@bp.get("/history/<record_type>/<record_id>")
def history(record_type, record_id):
    """F9.3: current record vs past revisions and verdict/rights events for any record."""
    if record_type not in db.RECORD_TYPES:
        return _error(404, "unknown_record_type", "알 수 없는 기록 유형입니다.")
    with closing(_conn()) as conn:
        current = db.get_record(conn, record_type, record_id)
        if current is None:
            return _error(404, "not_found", "기록을 찾을 수 없습니다.")
        revisions = _revisions(conn, record_type, record_id)
        events = []
        rights_list = []
        if record_type in SUBJECT_TYPES:
            rows = conn.execute(
                "SELECT * FROM rights_events WHERE subject_type = ? AND subject_id = ? ORDER BY at, id",
                (record_type, record_id),
            ).fetchall()
            events = [dict(r) for r in rows]
            rights_list = [
                {"id": rid, "version": db.get_record(conn, "rights", rid)["version"]}
                for rid in _rights_ids_for(conn, record_type, record_id)
            ]
        elif record_type == "rights":
            events = _events_for_rights(conn, record_id)
    return jsonify(
        current={"id": current["id"], "type": record_type, "title": current["title"],
                 "version": current["version"], "material_hash": current["material_hash"],
                 "status": current["status"], "synthetic": current["synthetic"]},
        revisions=revisions,
        events=events,
        rights=rights_list,
    )
