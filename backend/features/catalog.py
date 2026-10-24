"""S4 judgement-task catalog (EPIC 5, F4.1~F4.5). Stored as shared `catalog` records.

Card fields live in `fields`; links to materials/experts/works go through record_links.
Readiness (ready/hold/draft) is computed by the server and never implies rights or demand approval.
"""

import re
from contextlib import closing

from flask import Blueprint, current_app, jsonify, request

from backend import db
from backend.features import aidraft

bp = Blueprint("catalog", __name__, url_prefix="/api/catalog")

MAX_TITLE = 120
MAX_TEXT = 2000
MAX_ITEMS = 20

REQUIRED_CORE = [
    ("title", "제목"), ("domain", "분야"), ("summary", "요약"),
    ("judgement_task", "구매할 판단 과제"), ("target_capability", "대상 능력"),
    ("expected_deliverables", "예상 납품물"),
]
APPRENTICE_KEYS = [
    ("apprentice_context", "도제 학습 맥락"), ("novice_gap", "초보자 차이"),
    ("tacit_signals", "숙련 단서"), ("evidence_refs", "근거"),
]
AXES = [
    ("origin", "도제 경험 기원"),
    ("documentation_gap", "비표준/문서화 공백"),
    ("field_need", "현장 기본 필요성/오판 비용"),
    ("novice_difference", "숙련자-초보자 차이"),
    ("outcome_verifiability", "결과 검증 가능성"),
    ("lab_link", "Lab 평가 능력 연결"),
]
AXIS_KEYS = [k for k, _ in AXES]
UNKNOWN_WORD = "미확인"
URL_RE = re.compile(r"^https?://\S+$")


class ValidationError(Exception):
    def __init__(self, fields, message="입력을 확인해 주세요."):
        super().__init__(message)
        self.fields = fields
        self.message = message


class LinkRejected(Exception):
    def __init__(self, fields):
        super().__init__("link rejected")
        self.fields = fields


def _error(status, code, message, **extra):
    return jsonify(error={"code": code, "message": message, **extra}), status


def _conn():
    return db.connect(current_app.config["DB_PATH"])


def _text(data, key, errors, *, required=False, limit=MAX_TEXT, label=None):
    value = data.get(key)
    if value is None:
        if required:
            errors[key] = "필수 항목입니다."
        return None
    if not isinstance(value, str):
        errors[key] = "문자열이어야 합니다."
        return None
    value = value.strip()
    if required and not value:
        errors[key] = "필수 항목입니다. 공백만 입력할 수 없습니다."
        return None
    if len(value) > limit:
        errors[key] = f"{limit}자 이하로 입력해 주세요."
        return None
    return value


def _str_list(data, key, errors):
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        value = [line.strip() for line in value.splitlines() if line.strip()]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        errors[key] = "문자열 목록이어야 합니다."
        return None
    value = [v.strip() for v in value if v.strip()]
    if len(value) > MAX_ITEMS or any(len(v) > MAX_TEXT for v in value):
        errors[key] = f"최대 {MAX_ITEMS}개, 항목당 {MAX_TEXT}자까지입니다."
        return None
    return value


def _media_refs(data, errors):
    value = data.get("media_refs")
    if value is None:
        return None
    if isinstance(value, str):
        items = []
        for n, line in enumerate(value.splitlines(), 1):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) != 3:
                errors["media_refs"] = f"{n}번째 줄은 '주소 | 시점 | 설명' 형식이어야 합니다."
                return None
            items.append({"address": parts[0], "captured_at": parts[1], "description": parts[2]})
        value = items
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        errors["media_refs"] = f"매체 참조는 최대 {MAX_ITEMS}개 목록이어야 합니다."
        return None
    out = []
    for i, item in enumerate(value, 1):
        if not isinstance(item, dict) or set(item) - {"address", "captured_at", "description"}:
            errors["media_refs"] = f"{i}번째 매체 참조 형식이 올바르지 않습니다."
            return None
        address = str(item.get("address") or "").strip()
        captured = str(item.get("captured_at") or "").strip()
        description = str(item.get("description") or "").strip()
        if not URL_RE.match(address):
            errors["media_refs"] = f"{i}번째 매체의 주소는 http(s):// 로 시작해야 합니다."
            return None
        if not captured or not description:
            errors["media_refs"] = f"{i}번째 매체는 시점과 설명이 모두 필요합니다."
            return None
        out.append({"address": address[:MAX_TEXT], "captured_at": captured[:200],
                    "description": description[:MAX_TEXT]})
    return out


def _axes(data, errors):
    value = data.get("axes")
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - set(AXIS_KEYS):
        errors["axes"] = "발굴 축 키가 올바르지 않습니다."
        return None
    out = {}
    for key in value:
        raw = value[key]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            out[key] = {"status": "missing", "note": ""}
            continue
        if not isinstance(raw, str) or len(raw) > MAX_TEXT:
            errors["axes"] = "각 축은 근거 문자열 또는 '미확인'이어야 합니다."
            return None
        note = raw.strip()
        if note == UNKNOWN_WORD or note.startswith(UNKNOWN_WORD + ":"):
            out[key] = {"status": "unknown", "note": note}
        else:
            out[key] = {"status": "evidence", "note": note}
    return out


def _normalize_fields(data):
    """Validate the editable card payload. Returns (changes, errors); only keys present are returned."""
    errors = {}
    changes = {}
    if "title" in data:
        title = _text(data, "title", errors, required=True, limit=MAX_TITLE)
        if title is not None:
            changes["title"] = title
    for key, limit in [("domain", MAX_TITLE), ("summary", MAX_TEXT), ("judgement_task", MAX_TEXT),
                       ("target_capability", MAX_TITLE), ("expected_deliverables", MAX_TEXT),
                       ("apprentice_context", MAX_TEXT), ("novice_gap", MAX_TEXT)]:
        if key in data:
            value = _text(data, key, errors, required=True, limit=limit)
            if value is not None:
                changes[key] = value
    for key in ["tacit_signals", "evidence_refs"]:
        if key in data:
            value = _str_list(data, key, errors)
            if value is not None:
                changes[key] = value
    if "media_refs" in data:
        value = _media_refs(data, errors)
        if value is not None:
            changes["media_refs"] = value
    if "axes" in data:
        value = _axes(data, errors)
        if value is not None:
            changes["axes"] = value
    if "synthetic" in data and not isinstance(data["synthetic"], bool):
        errors["synthetic"] = "true 또는 false여야 합니다."
    if "title" in changes and "target_capability" in changes and \
            changes["title"] == changes["target_capability"]:
        errors["title"] = "제목이 대상 능력과 같습니다. 단순 기술명 대신 구매할 판단 과제를 적어 주세요."
    if errors:
        raise ValidationError(errors)
    return changes


def _card_status(item):
    """Server-side readiness. Returns (status, hold_reasons, missing_core, limits)."""
    fields = item["_fields"]
    missing = []
    for key, label in REQUIRED_CORE:
        value = item.get("title") if key == "title" else fields.get(key)
        if not value or not str(value).strip():
            missing.append(f"{label} 입력 필요")
    if missing:
        return "draft", missing, [], []
    hold = []
    links = item["_links"]
    if not links["sources"]:
        hold.append("연결된 원천 자료가 없습니다. S2에서 자료를 확인해 연결하세요.")
    if not links["experts"]:
        hold.append("승인된 전문가가 연결되지 않았습니다.")
    if not links["works"]:
        hold.append("연결된 원시 작업이 없습니다.")
    for key, label in APPRENTICE_KEYS:
        if not fields.get(key):
            hold.append(f"{label}이(가) 비어 있습니다.")
    if not fields.get("media_refs") and not fields.get("evidence_refs"):
        hold.append("근거 참조 또는 매체 참조가 필요합니다.")
    status = "hold" if hold else "ready"
    limits = []
    axes = fields.get("axes") or {}
    unknown = [label for key, label in AXES if (axes.get(key) or {}).get("status") != "evidence"]
    if unknown:
        limits.append("발굴 6축 중 근거 없음·미확인: " + ", ".join(unknown))
    if fields.get("media_refs"):
        limits.append("매체 참조는 주소·시점·설명 메타데이터입니다. 자동 영상 분석은 수행되지 않았습니다.")
    limits.append("발굴 점수와 준비 상태는 전문성·수요·권리 승인이 아닙니다.")
    return status, hold, [], limits


def _fields_of(record):
    return {k: v for k, v in record.items() if k not in db_core_keys()}


def db_core_keys():
    return {"id", "type", "title", "status", "synthetic", "version", "material_hash",
            "input_hash", "body", "created_at", "updated_at"}


def _detail(conn, record):
    """Build the S4 detail payload: card fields, computed readiness, links with reasons."""
    card_id = record["id"]
    fields = _fields_of(record)
    links, missing = db.relations(conn, "catalog", card_id)
    link_map = {"sources": [], "experts": [], "works": []}
    for link in links:
        if link["type"] == "materials" and link["relation"] == "from":
            link_map["sources"].append(link)
        elif link["type"] == "experts" and link["relation"] == "with_expert":
            link_map["experts"].append(link)
        elif link["type"] == "works" and link["relation"] == "for":
            link_map["works"].append(link)
    item = {**record, "_fields": fields, "_links": link_map}
    status, hold, _, limits = _card_status(item)
    axes = fields.get("axes") or {}
    axis_view = []
    for key, label in AXES:
        entry = axes.get(key) or {"status": "missing", "note": ""}
        axis_view.append({"key": key, "label": label, "status": entry["status"], "note": entry["note"]})
    return {
        "item": record,
        "card": {
            "status": status,
            "hold_reasons": hold,
            "limits": limits,
            "evidence_axis_count": sum(1 for a in axis_view if a["status"] == "evidence"),
        },
        "axes": axis_view,
        "links": link_map,
        "missing_links": missing,
    }


def _summary_item(conn, record):
    detail = _detail(conn, record)
    return {
        "id": record["id"], "title": record["title"], "domain": _fields_of(record).get("domain"),
        "synthetic": record["synthetic"], "version": record["version"],
        "card_status": detail["card"]["status"],
        "evidence_axis_count": detail["card"]["evidence_axis_count"],
        "updated_at": record["updated_at"],
    }


def _check_source(conn, sid):
    rec = db.get_record(conn, "materials", sid)
    if rec is None:
        raise LinkRejected({"source_ids": f"{sid}: 존재하지 않는 자료입니다."})


def _check_expert(conn, eid):
    rec = db.get_record(conn, "experts", eid)
    if rec is None:
        raise LinkRejected({"expert_ids": f"{eid}: 존재하지 않는 전문가입니다."})
    if rec["status"] != "approved":
        raise LinkRejected({"expert_ids": f"{eid}: 승인되지 않은 전문가는 연결할 수 없습니다."})


def _check_work(conn, wid, expert_ids, source_ids):
    rec = db.get_record(conn, "works", wid)
    if rec is None:
        raise LinkRejected({"work_ids": f"{wid}: 존재하지 않는 작업이거나 작업 유형이 아닙니다."})
    by = [r["to_id"] for r in conn.execute(
        "SELECT to_id FROM record_links WHERE from_type='works' AND from_id=? AND to_type='experts' "
        "AND relation='by'", (wid,)).fetchall()]
    if rec.get("expert_id"):
        by.append(rec["expert_id"])
    if not by:
        raise LinkRejected({"work_ids": f"{wid}: 작업을 올린 전문가가 연결되어 있지 않습니다."})
    for eid in by:
        expert = db.get_record(conn, "experts", eid)
        if expert is None or expert["status"] != "approved":
            raise LinkRejected({"work_ids": f"{wid}: 승인되지 않은 전문가의 작업은 연결할 수 없습니다."})
        if eid not in expert_ids:
            raise LinkRejected({"work_ids": f"{wid}: 작업의 전문가({eid})가 전문가 목록에 없습니다."})
    mats = [r["to_id"] for r in conn.execute(
        "SELECT to_id FROM record_links WHERE from_type='works' AND from_id=? AND to_type='materials' "
        "AND relation='from'", (wid,)).fetchall()]
    for mid in mats:
        if mid not in source_ids:
            raise LinkRejected({"source_ids": f"{wid}의 원문 자료({mid})가 자료 목록에 없습니다."})


def _id_list(data, key, errors):
    value = data.get(key, [])
    if value is None:
        value = []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        errors[key] = "ID 문자열 목록이어야 합니다."
        return []
    if len(value) > MAX_ITEMS:
        errors[key] = f"최대 {MAX_ITEMS}개까지 연결할 수 있습니다."
    return list(dict.fromkeys(v.strip() for v in value))


def _json_body():
    if not request.is_json:
        return None, _error(415, "unsupported_media_type", "application/json 요청만 받습니다.")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, _error(400, "invalid_json", "JSON 객체를 보내 주세요.")
    return data, None


def _stored_fields(record_fields, changes):  # noqa: D401
    merged = dict(record_fields)
    merged.update(changes)
    return merged


def _store_refs(conn, card_id, source_ids, expert_ids, work_ids, old):
    """Replace this card's link set. Only links this card owns are removed."""
    for sid in old["sources"]:
        if sid not in source_ids:
            conn.execute("DELETE FROM record_links WHERE from_type='catalog' AND from_id=? "
                         "AND to_type='materials' AND to_id=? AND relation='from'", (card_id, sid))
    for eid in old["experts"]:
        if eid not in expert_ids:
            conn.execute("DELETE FROM record_links WHERE from_type='catalog' AND from_id=? "
                         "AND to_type='experts' AND to_id=? AND relation='with_expert'", (card_id, eid))
    for wid in old["works"]:
        if wid not in work_ids:
            conn.execute("DELETE FROM record_links WHERE from_type='works' AND from_id=? "
                         "AND to_type='catalog' AND to_id=? AND relation='for'", (wid, card_id))
    for sid in source_ids:
        db.add_link(conn, "catalog", card_id, "materials", sid, "from")
    for eid in expert_ids:
        db.add_link(conn, "catalog", card_id, "experts", eid, "with_expert")
    for wid in work_ids:
        db.add_link(conn, "works", wid, "catalog", card_id, "for")


def _old_refs(record_fields):
    return {"sources": list(record_fields.get("source_ids") or []),
            "experts": list(record_fields.get("expert_ids") or []),
            "works": list(record_fields.get("work_ids") or [])}


@bp.get("/")
def list_cards():
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 100)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        return _error(400, "invalid_query", "limit/offset은 정수여야 합니다.")
    q = (request.args.get("q") or "").strip().lower()
    status_filter = (request.args.get("status") or "").strip()
    domain_filter = (request.args.get("domain") or "").strip()
    with closing(_conn()) as conn:
        records, _ = db.list_records(conn, "catalog", limit=100, offset=0)
        items = []
        for brief in records:
            record = db.get_record(conn, "catalog", brief["id"])
            entry = _summary_item(conn, record)
            if q and q not in (record["title"] or "").lower() and q not in str(record.get("summary") or "").lower():
                continue
            if status_filter and entry["card_status"] != status_filter:
                continue
            if domain_filter and entry["domain"] != domain_filter:
                continue
            items.append(entry)
    total = len(items)
    return jsonify(items=items[offset:offset + limit], total=total)


def _create(data, run=None):
    """Validate and store a new card. `run` (an ai_draft_runs row) marks it as an adopted AI draft."""
    try:
        errors = {}
        changes = _normalize_fields(data)
        missing = [k for k, _ in REQUIRED_CORE if k not in changes]
        if missing:
            errors.update({k: "필수 항목입니다." for k in missing})
            raise ValidationError(errors)
    except ValidationError as exc:
        return _error(400, "validation_failed", exc.message, fields=exc.fields)
    synthetic = bool(data.get("synthetic", False))
    title = changes.pop("title")
    fields = dict(changes)
    fields.setdefault("axes", {})
    fields.setdefault("media_refs", [])
    fields.setdefault("tacit_signals", [])
    fields.setdefault("evidence_refs", [])
    fields.update({"source_ids": [], "expert_ids": [], "work_ids": []})
    with closing(_conn()) as conn, db.write_transaction(conn):
        if run is not None:
            fresh = aidraft.get_run(conn, run["id"], "F10.4")
            if fresh["adopted_at"]:
                return _error(409, "already_adopted", "이미 채택한 AI 초안입니다.")
            source_ids = list(fresh["detail"].get("source_ids") or [])
            fields["source_ids"] = source_ids
            fields["ai_draft"] = {"run_id": run["id"], "model": run["model"],
                                  "label": aidraft.label(run["model"]),
                                  "note": "AI 초안을 사람이 검토·수정해 채택한 카드입니다. 권리·수요·전문성 승인이 아닙니다."}
        card_id = db.create_record(conn, "catalog", title=title, status="draft",
                                   synthetic=synthetic, body=fields.get("summary"), fields=fields)
        if run is not None:
            _store_refs(conn, card_id, fields["source_ids"], [], [], {"sources": [], "experts": [], "works": []})
            aidraft.mark_adopted(conn, run["id"])
        record = db.get_record(conn, "catalog", card_id)
        detail = _detail(conn, record)
        conn.execute("UPDATE records SET status = ? WHERE type='catalog' AND id=?",
                     (detail["card"]["status"], card_id))
        detail = _detail(conn, db.get_record(conn, "catalog", card_id))
    return jsonify(detail), 201


@bp.post("/")
def create_card():
    data, err = _json_body()
    if err:
        return err
    return _create(data)


DRAFT_KEYS = ("title", "domain", "summary", "judgement_task", "target_capability", "expected_deliverables")
DRAFT_SYSTEM = (
    "당신은 도제식 숙련 판단을 구매 가능한 판단 과제로 정리하는 보조자입니다. 주어진 자료만 근거로 "
    "JSON 객체 하나만 출력합니다. 키: title, domain, summary, judgement_task, target_capability, "
    "expected_deliverables. 자료에 없는 사실·가격·권리를 만들지 않습니다.")


@bp.get("/ai-status")
def ai_status():
    return jsonify(aidraft.status("F10.4"))


@bp.post("/ai-draft")
def ai_draft():
    """F10.4: draft a task card from linked source materials. Nothing is saved until a person adopts it."""
    data, err = _json_body()
    if err:
        return err
    ids = data.get("source_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(v, str) and v.strip() for v in ids) \
            or len(ids) > MAX_ITEMS:
        return _error(422, "validation_failed", "초안의 원천 자료를 1개 이상 선택해 주세요.",
                      fields={"source_ids": "자료 ID 목록이 필요합니다."})
    ids = list(dict.fromkeys(v.strip() for v in ids))
    with closing(_conn()) as conn, db.write_transaction(conn):
        mats = []
        for sid in ids:
            rec = db.get_record(conn, "materials", sid)
            if rec is None:
                return _error(422, "validation_failed", f"{sid}: 존재하지 않는 자료입니다.",
                              fields={"source_ids": f"{sid}: 존재하지 않는 자료입니다."})
            mats.append(rec)
        prompt = "\n\n".join(f"[자료 {m['id']}] {m['title']}\n{(m.get('body') or '')[:1500]}" for m in mats)
        parsed, info = aidraft.draft(conn, "F10.4", DRAFT_SYSTEM, prompt, "catalog", None,
                                     detail={"source_ids": ids})
    draft = None
    if parsed is not None:
        draft = {}
        limits = {"title": MAX_TITLE, "domain": MAX_TITLE, "target_capability": MAX_TITLE}
        for key in DRAFT_KEYS:
            value = aidraft.clip(parsed.get(key), limits.get(key, MAX_TEXT))
            if value:
                draft[key] = value
        if not draft:
            draft = None
    if draft is None and info["ok"]:
        info.update(ok=False, error="empty_draft", label=aidraft.label(None), mode="manual")
    return jsonify(draft=draft, source_ids=ids, ai=info, adoptable=draft is not None,
                   notice="AI 초안은 저장되지 않았습니다. 읽고 수정한 뒤 채택해야 카드가 만들어집니다. "
                          "수동 작성은 언제든 쓸 수 있습니다.")


@bp.post("/ai-draft/<run_id>/adopt")
def ai_draft_adopt(run_id):
    data, err = _json_body()
    if err:
        return err
    with closing(_conn()) as conn:
        run = aidraft.get_run(conn, run_id, "F10.4")
    if run is None or not run["ok"]:
        return _error(404, "not_found", "채택할 수 있는 AI 초안을 찾을 수 없습니다.")
    return _create(data, run)


@bp.get("/<card_id>")
def get_card(card_id):
    with closing(_conn()) as conn:
        record = db.get_record(conn, "catalog", card_id)
        if record is None:
            return _error(404, "not_found", "과제 카드를 찾을 수 없습니다.")
        return jsonify(_detail(conn, record))


@bp.patch("/<card_id>")
def update_card(card_id):
    data, err = _json_body()
    if err:
        return err
    expected = data.pop("expected_version", None)
    if not isinstance(expected, int) or isinstance(expected, bool):
        return _error(400, "validation_failed", "expected_version이 필요합니다.",
                      fields={"expected_version": "정수여야 합니다."})
    try:
        changes = _normalize_fields(data)
    except ValidationError as exc:
        return _error(400, "validation_failed", exc.message, fields=exc.fields)
    with closing(_conn()) as conn:
        try:
            with db.write_transaction(conn):
                record = db.get_record(conn, "catalog", card_id)
                if record is None:
                    return _error(404, "not_found", "과제 카드를 찾을 수 없습니다.")
                if record["version"] != expected:
                    return _error(409, "version_conflict",
                                  f"다른 변경이 먼저 저장되었습니다(현재 버전 {record['version']}). 다시 열어 주세요.")
                old_fields = _fields_of(record)
                title = changes.pop("title", record["title"])
                new_fields = _stored_fields(old_fields, {k: v for k, v in changes.items() if k != "title"})
                body = new_fields.get("summary")
                db.update_record(conn, "catalog", card_id, expected_version=expected,
                                 changes={"title": title, "body": body, "fields": new_fields})
                updated = db.get_record(conn, "catalog", card_id)
                detail = _detail(conn, updated)
                conn.execute("UPDATE records SET status = ? WHERE type='catalog' AND id=?",
                             (detail["card"]["status"], card_id))
        except db.RecordConflict as exc:
            return _error(409, "version_conflict", str(exc))
        detail = _detail(conn, db.get_record(conn, "catalog", card_id))
    return jsonify(detail)


@bp.post("/<card_id>/link")
def link_card(card_id):
    data, err = _json_body()
    if err:
        return err
    errors = {}
    expected = data.get("expected_version")
    if not isinstance(expected, int) or isinstance(expected, bool):
        errors["expected_version"] = "정수여야 합니다."
    source_ids = _id_list(data, "source_ids", errors)
    expert_ids = _id_list(data, "expert_ids", errors)
    work_ids = _id_list(data, "work_ids", errors)
    if errors:
        return _error(400, "validation_failed", "연결 입력을 확인해 주세요.", fields=errors)
    with closing(_conn()) as conn:
        try:
            with db.write_transaction(conn):
                record = db.get_record(conn, "catalog", card_id)
                if record is None:
                    return _error(404, "not_found", "과제 카드를 찾을 수 없습니다.")
                if record["version"] != expected:
                    return _error(409, "version_conflict", "다른 변경이 먼저 저장되었습니다. 다시 열어 주세요.")
                for sid in source_ids:
                    _check_source(conn, sid)
                for eid in expert_ids:
                    _check_expert(conn, eid)
                for wid in work_ids:
                    _check_work(conn, wid, set(expert_ids), set(source_ids))
                old_fields = _fields_of(record)
                _store_refs(conn, card_id, source_ids, expert_ids, work_ids, _old_refs(old_fields))
                new_fields = _stored_fields(old_fields, {"source_ids": source_ids,
                                                         "expert_ids": expert_ids, "work_ids": work_ids})
                db.update_record(conn, "catalog", card_id, expected_version=expected,
                                 changes={"fields": new_fields})
                detail = _detail(conn, db.get_record(conn, "catalog", card_id))
                conn.execute("UPDATE records SET status = ? WHERE type='catalog' AND id=?",
                             (detail["card"]["status"], card_id))
        except LinkRejected as exc:
            return _error(422, "link_rejected", "맞지 않는 연결이 있어 저장하지 않았습니다.",
                          field_errors=exc.fields)
        except db.RecordConflict as exc:
            return _error(409, "version_conflict", str(exc))
        detail = _detail(conn, db.get_record(conn, "catalog", card_id))
    return jsonify(detail)


def init_schema(conn):
    """Card data lives in the shared records store; only the AI attempt log is extra."""
    aidraft.init_schema(conn)
