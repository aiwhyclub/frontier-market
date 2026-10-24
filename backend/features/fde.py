"""S6 FDE 프로젝트 (EPIC 8, F6.1~F6.4).

Projects live in the shared `projects` record type. Type-specific data is kept in fields_json.
Links: project -> catalog ("catalog"), -> works ("work"), -> experts ("expert").
Stage history is stored as fields.stage_log; each entry carries its basis label
(planned / synthetic / actual). Enterprise AX cost is fixed at 0 and nonzero input is rejected.
"""

import re
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request

from backend import db

bp = Blueprint("fde", __name__, url_prefix="/api/fde")

MAX_JSON_BYTES = 256 * 1024
MAX_TITLE = 120
MAX_SHORT = 200
MAX_LONG = 2000
MAX_IDS = 20
MAX_STAGE_LOG = 200
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

STAGE_KINDS = {"plan": "계획", "progress": "진행", "review": "검토", "site_result": "현장 결과"}
BASIS_LABELS = {
    "planned": "계획값",
    "synthetic": "합성 현장",
    "actual": "사람이 기록한 실제 결과",
}
# plan is always a planned value; the other stages may be synthetic or a human-recorded actual.
STAGE_BASIS = {
    "plan": {"planned"},
    "progress": {"synthetic", "actual"},
    "review": {"synthetic", "actual"},
    "site_result": {"synthetic", "actual"},
}
EVIDENCE_REQUIRED = {"progress", "review", "site_result"}

CREATE_KEYS = {
    "title", "synthetic", "catalog_id", "work_ids", "expert_ids", "company_name", "objective",
    "scope", "deliverables", "owner", "notes", "site_name", "planned_start", "planned_end",
    "company_ax_fee",
}
PATCH_TEXT_KEYS = {
    "title": MAX_TITLE, "company_name": MAX_SHORT, "site_name": MAX_SHORT, "owner": MAX_SHORT,
    "objective": MAX_LONG, "scope": MAX_LONG, "deliverables": MAX_LONG, "notes": MAX_LONG,
    "enterprise_outcome": MAX_LONG, "buyer_delivery": MAX_LONG,
}
PATCH_KEYS = set(PATCH_TEXT_KEYS) | {"expected_version", "company_ax_fee"}
FEE_KEYS = {"company_ax_fee"}
REFUSED_KEYS = {"price", "cost", "price_minor", "cost_minor", "currency"}


CORE_KEYS = {"id", "type", "title", "status", "synthetic", "version", "material_hash", "input_hash",
             "body", "created_at", "updated_at"}


def _fields_of(item):
    """db.get_record flattens stored fields next to the core keys; this takes the stored fields back."""
    return {k: v for k, v in item.items() if k not in CORE_KEYS}


def _error(status, code, message, **extra):
    return jsonify(error={"code": code, "message": message, **extra}), status


def _conn():
    from flask import current_app
    return db.connect(current_app.config["DB_PATH"])


def _json_body():
    if request.mimetype != "application/json":
        return None, _error(415, "unsupported_media_type", "application/json 으로 보내 주세요.")
    if request.content_length and request.content_length > MAX_JSON_BYTES:
        return None, _error(413, "payload_too_large", "요청이 너무 큽니다.")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, _error(400, "bad_json", "JSON 객체를 보내 주세요.")
    return data, None


def _text(data, key, limit, errors, required=False):
    value = data.get(key)
    if value is None:
        if required:
            errors[key] = "필수 항목입니다."
        return ""
    if not isinstance(value, str):
        errors[key] = "문자열이어야 합니다."
        return ""
    value = value.strip()
    if required and not value:
        errors[key] = "필수 항목입니다."
    elif len(value) > limit:
        errors[key] = f"{limit}자 이하로 입력해 주세요."
    return value


def _id_list(data, key, errors, required=False):
    value = data.get(key)
    if value is None or value == []:
        if required:
            errors[key] = "1개 이상 선택해 주세요."
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        errors[key] = "ID 목록이어야 합니다."
        return []
    ids = []
    for v in value:
        v = v.strip()
        if v not in ids:
            ids.append(v)
    if len(ids) > MAX_IDS:
        errors[key] = f"{MAX_IDS}개 이하로 선택해 주세요."
    if required and not ids:
        errors[key] = "1개 이상 선택해 주세요."
    return ids


def _fee_is_zero(value):
    """Accept only a zero amount (0, "0", "0.00", "0원"). Anything else is refused."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        cleaned = value.strip().replace("원", "").replace(",", "")
        try:
            return Decimal(cleaned) == 0
        except InvalidOperation:
            return False
    return False


def _check_fee(data, errors):
    if "company_ax_fee" in data and not _fee_is_zero(data["company_ax_fee"]):
        errors["company_ax_fee"] = "기업 AX 비용은 0원으로 고정됩니다. 유료 값은 받지 않습니다."


def _check_dates(start, end, errors):
    for key, value in (("planned_start", start), ("planned_end", end)):
        if value and not DATE_RE.match(value):
            errors[key] = "YYYY-MM-DD 형식으로 입력해 주세요."
    if not errors.get("planned_start") and not errors.get("planned_end") and start and end:
        try:
            if date.fromisoformat(end) < date.fromisoformat(start):
                errors["planned_end"] = "종료일이 시작일보다 빠를 수 없습니다."
        except ValueError:
            errors["planned_start"] = "존재하는 날짜를 입력해 주세요."


def _has_link(conn, from_type, from_id, to_type, to_id, relation):
    return conn.execute(
        "SELECT 1 FROM record_links WHERE from_type=? AND from_id=? AND to_type=? AND to_id=? AND relation=?",
        (from_type, from_id, to_type, to_id, relation),
    ).fetchone() is not None


def _project_or_404(conn, project_id):
    item = db.get_record(conn, "projects", project_id)
    return item


def _with_labels(item):
    fields = _fields_of(item)
    log = []
    for entry in fields.get("stage_log", []):
        e = dict(entry)
        e["kind_label"] = STAGE_KINDS.get(e.get("kind"), e.get("kind"))
        e["basis_label"] = BASIS_LABELS.get(e.get("basis"), e.get("basis"))
        log.append(e)
    fields["stage_log"] = log
    fields["stage_label"] = STAGE_KINDS.get(fields.get("stage"), fields.get("stage") or "계획")
    fields["company_ax_fee"] = {
        "amount": 0,
        "label": "기업 AX 비용 0원 (무료 AX, 고정)",
    }
    fields["labels"] = {
        "basis": BASIS_LABELS,
        "note": "계획·합성 현장·사람이 기록한 실제 결과는 구분해 표시합니다. 기록은 사람이 남긴 값이며 시스템이 실제 설치나 납품을 수행했다는 뜻이 아닙니다.",
        "enterprise_outcome_note": "기업용 개선/인수 결과는 구매자 납품·검수와 분리해 기록합니다.",
        "buyer_delivery_note": "구매자 납품·검수는 기업용 개선 결과와 별도로 기록합니다.",
    }
    out = {k: v for k, v in item.items() if k in CORE_KEYS}
    out["fields"] = fields
    return out


@bp.get("/options")
def options():
    """Reference lists for the create form: catalogs, works and experts that can be linked."""
    with closing(_conn()) as conn:
        out = {}
        for rtype in ("catalog", "works", "experts"):
            items, _total = db.list_records(conn, rtype, limit=100)
            out[rtype] = [{"id": i["id"], "title": i["title"], "status": i["status"],
                           "synthetic": i["synthetic"]} for i in items]
    return jsonify(options=out)


@bp.get("/projects")
def list_projects():
    with closing(_conn()) as conn:
        items, total = db.list_records(conn, "projects", limit=100)
        rows = []
        for summary in items:
            full = db.get_record(conn, "projects", summary["id"])
            fields = _fields_of(full)
            rows.append({**summary, "stage": fields.get("stage", "plan"),
                         "stage_label": STAGE_KINDS.get(fields.get("stage", "plan"), "계획"),
                         "catalog_id": fields.get("catalog_id"),
                         "company_ax_fee": {"amount": 0}})
    return jsonify(items=rows, total=total)


@bp.get("/projects/<project_id>")
def get_project(project_id):
    with closing(_conn()) as conn:
        item = _project_or_404(conn, project_id)
        if item is None:
            return _error(404, "not_found", "프로젝트를 찾을 수 없습니다.")
        links, missing = db.relations(conn, "projects", project_id)
    return jsonify(item=_with_labels(item), links=links, missing_links=missing)


@bp.post("/projects")
def create_project():
    data, err = _json_body()
    if err:
        return err
    errors = {}
    unknown = set(data) - CREATE_KEYS
    refused = unknown & REFUSED_KEYS
    if refused:
        return _error(422, "refused_field", "선택 F6.5 손익 값은 이 화면에서 받지 않습니다.",
                      fields={k: "받지 않는 항목입니다." for k in sorted(refused)})
    if unknown - REFUSED_KEYS:
        return _error(422, "unknown_field", "허용되지 않은 항목이 있습니다.",
                      fields={k: "허용되지 않은 항목입니다." for k in sorted(unknown - REFUSED_KEYS)})
    if data.get("synthetic") is not True:
        errors["synthetic"] = "현재는 합성 프로젝트만 등록할 수 있습니다(true)."
    title = _text(data, "title", MAX_TITLE, errors, required=True)
    catalog_id = _text(data, "catalog_id", MAX_SHORT, errors, required=True)
    work_ids = _id_list(data, "work_ids", errors, required=True)
    expert_ids = _id_list(data, "expert_ids", errors)
    values = {k: _text(data, k, MAX_LONG, errors) for k in
              ("company_name", "objective", "scope", "deliverables", "owner", "notes", "site_name")}
    values["company_name"] = values["company_name"] or ""
    start = _text(data, "planned_start", 10, errors)
    end = _text(data, "planned_end", 10, errors)
    _check_dates(start, end, errors)
    _check_fee(data, errors)

    if errors:
        return _error(422, "validation_error", "입력을 확인해 주세요.", fields=errors)

    with closing(_conn()) as conn:
        with db.write_transaction(conn):
            if db.get_record(conn, "catalog", catalog_id) is None:
                errors["catalog_id"] = "카탈로그를 찾을 수 없습니다."
            for wid in work_ids:
                if db.get_record(conn, "works", wid) is None:
                    errors["work_ids"] = f"작업을 찾을 수 없습니다: {wid}"
                    break
                if not _has_link(conn, "works", wid, "catalog", catalog_id, "for"):
                    errors["work_ids"] = f"작업 {wid}이(가) 이 카탈로그와 연결돼 있지 않습니다."
                    break
            for eid in expert_ids:
                if db.get_record(conn, "experts", eid) is None:
                    errors["expert_ids"] = f"전문가를 찾을 수 없습니다: {eid}"
                    break
            if errors:
                return _error(422, "validation_error", "연결 관계를 확인해 주세요.", fields=errors)

            fields = {
                "catalog_id": catalog_id,
                "work_refs": work_ids,
                "expert_refs": expert_ids,
                "company_name": values["company_name"],
                "objective": values["objective"],
                "scope": values["scope"],
                "deliverables": values["deliverables"],
                "owner": values["owner"],
                "notes": values["notes"],
                "site_name": values["site_name"],
                "planned_start": start or None,
                "planned_end": end or None,
                "stage": "plan",
                "stage_log": [],
                "enterprise_outcome": "",
                "buyer_delivery": "",
                "actual_result": "",
                "company_ax_fee_minor": 0,
            }
            rid = db.create_record(conn, "projects", title=title, status="scoping", synthetic=True,
                                   body=None, fields=fields)
            db.add_link(conn, "projects", rid, "catalog", catalog_id, "catalog")
            for wid in work_ids:
                db.add_link(conn, "projects", rid, "works", wid, "work")
            for eid in expert_ids:
                db.add_link(conn, "projects", rid, "experts", eid, "expert")
            item = db.get_record(conn, "projects", rid)
    return jsonify(item=_with_labels(item)), 201


@bp.patch("/projects/<project_id>")
def update_project(project_id):
    data, err = _json_body()
    if err:
        return err
    errors = {}
    unknown = set(data) - PATCH_KEYS
    if unknown & REFUSED_KEYS:
        return _error(422, "refused_field", "선택 F6.5 손익 값은 이 화면에서 받지 않습니다.")
    if unknown:
        return _error(422, "unknown_field", "수정할 수 없는 항목이 있습니다.",
                      fields={k: "수정할 수 없는 항목입니다." for k in sorted(unknown)})
    expected = data.get("expected_version")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        return _error(422, "validation_error", "expected_version을 보내 주세요.",
                      fields={"expected_version": "양의 정수여야 합니다."})
    changes = {}
    for key, limit in PATCH_TEXT_KEYS.items():
        if key in data:
            changes[key] = _text(data, key, limit, errors, required=(key == "title"))
    _check_fee(data, errors)
    if errors:
        return _error(422, "validation_error", "입력을 확인해 주세요.", fields=errors)

    with closing(_conn()) as conn:
        with db.write_transaction(conn):
            current = db.get_record(conn, "projects", project_id)
            if current is None:
                return _error(404, "not_found", "프로젝트를 찾을 수 없습니다.")
            fields = _fields_of(current)
            title = changes.pop("title", None)
            for key, value in changes.items():
                fields[key] = value
            try:
                kwargs = {"fields": fields}
                if title is not None:
                    kwargs["title"] = title
                db.update_record(conn, "projects", project_id, expected_version=expected, changes=kwargs)
            except db.RecordConflict:
                return _error(409, "version_conflict", "다른 변경이 먼저 저장됐습니다. 새로 불러와 주세요.")
            item = db.get_record(conn, "projects", project_id)
    return jsonify(item=_with_labels(item))


@bp.post("/projects/<project_id>/stages")
def add_stage(project_id):
    data, err = _json_body()
    if err:
        return err
    errors = {}
    unknown = set(data) - {"expected_version", "kind", "basis", "note", "evidence"}
    if unknown:
        return _error(422, "unknown_field", "허용되지 않은 항목이 있습니다.",
                      fields={k: "허용되지 않은 항목입니다." for k in sorted(unknown)})
    expected = data.get("expected_version")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        errors["expected_version"] = "양의 정수여야 합니다."
    kind = data.get("kind")
    if kind not in STAGE_KINDS:
        errors["kind"] = "plan, progress, review, site_result 중 하나여야 합니다."
    basis = data.get("basis")
    if kind in STAGE_BASIS and basis not in STAGE_BASIS[kind]:
        errors["basis"] = "이 단계에 허용되지 않은 근거 구분입니다."
    note = _text(data, "note", MAX_LONG, errors)
    evidence = _text(data, "evidence", MAX_LONG, errors)
    if kind in EVIDENCE_REQUIRED and not evidence:
        errors["evidence"] = "수행 근거를 입력해 주세요."
    if kind == "plan" and not note:
        errors["note"] = "계획 내용을 입력해 주세요."
    if errors:
        return _error(422, "validation_error", "입력을 확인해 주세요.", fields=errors)

    with closing(_conn()) as conn:
        with db.write_transaction(conn):
            current = db.get_record(conn, "projects", project_id)
            if current is None:
                return _error(404, "not_found", "프로젝트를 찾을 수 없습니다.")
            fields = _fields_of(current)
            log = list(fields.get("stage_log", []))
            if len(log) >= MAX_STAGE_LOG:
                return _error(422, "stage_log_full", "단계 기록이 가득 찼습니다.")
            log.append({"kind": kind, "basis": basis, "note": note, "evidence": evidence,
                        "recorded_at": db.now_iso()})
            fields["stage_log"] = log
            fields["stage"] = kind
            try:
                db.update_record(conn, "projects", project_id, expected_version=expected,
                                 changes={"fields": fields})
            except db.RecordConflict:
                return _error(409, "version_conflict", "다른 변경이 먼저 저장됐습니다. 새로 불러와 주세요.")
            item = db.get_record(conn, "projects", project_id)
    return jsonify(item=_with_labels(item)), 201
