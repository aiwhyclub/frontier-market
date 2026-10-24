"""S5 정제·검증 (EPIC 7, F5.1~F5.6). Blueprint prefix /api/refine.
AI refine draft (F10.2) is optional (backend.ai) and kept apart from original/approval; see ai_* below.

Refinement data lives in the work record's fields under "refinement":
  draft: {content, method: "rule", generated_at}           (not a review input)
  corrected: {section: text}                                (review input)
  correction: {reason, reviewer, at, from_draft}            (review input: reason)
  correction_history: [previous corrected blocks]
  ai_draft: {content, status: proposed|adopted|rejected, model, ...}   (never a review input)
  ai_attempts: [every AI call result, success or failure]
Review decisions and original revisions are stored in refine_reviews / refine_revisions.
input_hash = SHA-256 over FM-CJSON-like canonical JSON of the review inputs (see _input_hash).
"""

import hashlib
import json
import re

from flask import Blueprint, current_app, jsonify, request

from backend import ai, db

bp = Blueprint("refine", __name__, url_prefix="/api/refine")

SECTIONS = ["context", "observation", "novice_errors", "judgement", "outcome_check", "criteria", "questions"]
SECTION_LABELS = {
    "context": "맥락",
    "observation": "관찰 사실",
    "novice_errors": "초보자 오류",
    "judgement": "판단·예외",
    "outcome_check": "결과 확인",
    "criteria": "평가 기준",
    "questions": "추가 질문",
}
OPERATOR_KEY = "refine_operator"
MAX_SECTION_CHARS = 4000
MAX_REASON_CHARS = 500
MAX_HISTORY = 50
MAX_BODY_CHARS = 20000
CORE_KEYS = {"id", "type", "title", "status", "synthetic", "version", "material_hash",
             "input_hash", "body", "created_at", "updated_at"}

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS refine_reviews (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        work_id TEXT NOT NULL,
        work_version INTEGER NOT NULL,
        input_hash TEXT NOT NULL,
        decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
        notes TEXT NOT NULL,
        reviewer TEXT NOT NULL,
        evidence_refs_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS refine_reviews_work ON refine_reviews (work_id, seq)",
    """CREATE TABLE IF NOT EXISTS refine_revisions (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        work_id TEXT NOT NULL,
        from_version INTEGER NOT NULL,
        to_version INTEGER NOT NULL,
        reason TEXT NOT NULL,
        reviewer TEXT NOT NULL,
        changed_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS refine_revisions_work ON refine_revisions (work_id, seq)",
]


def init_schema(conn):
    for statement in SCHEMA:
        conn.execute(statement)


class _Fail(Exception):
    def __init__(self, status, code, message, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


@bp.errorhandler(_Fail)
def _handle_fail(exc):
    return jsonify(error={"code": exc.code, "message": exc.message, **exc.extra}), exc.status


def _conn():
    conn = db.connect(current_app.config["DB_PATH"])
    init_schema(conn)
    return conn


def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise _Fail(400, "bad_request", "JSON 객체를 보내 주세요.")
    return data


def _text(data, key, limit, required=False):
    value = data.get(key)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise _Fail(400, "validation", f"{key} 값은 문자열이어야 합니다.", fields=[key])
    value = value.strip()
    if required and not value:
        raise _Fail(400, "validation", f"{key} 값을 입력해 주세요.", fields=[key])
    if len(value) > limit:
        raise _Fail(400, "validation", f"{key} 값이 너무 깁니다.", fields=[key])
    return value


def _expected_version(data):
    value = data.get("expected_version")
    if not isinstance(value, int) or isinstance(value, bool):
        raise _Fail(400, "validation", "expected_version을 정수로 보내 주세요.", fields=["expected_version"])
    return value


def _raw_fields(conn, work_id):
    row = conn.execute("SELECT fields_json FROM records WHERE type = 'works' AND id = ?", (work_id,)).fetchone()
    if row is None:
        raise _Fail(404, "not_found", "작업을 찾을 수 없습니다.")
    return json.loads(row["fields_json"])


def _work(conn, work_id):
    item = db.get_record(conn, "works", work_id)
    if item is None:
        raise _Fail(404, "not_found", "작업을 찾을 수 없습니다.")
    return item


def _context(conn, work_id):
    links, _ = db.relations(conn, "works", work_id)

    def linked(relation, rtype):
        found = []
        for link in links:
            if link["relation"] == relation and link["type"] == rtype and link["direction"] == "out":
                rec = db.get_record(conn, rtype, link["id"])
                if rec is not None:
                    found.append(rec)
        return found

    return {
        "experts": linked("by", "experts"),
        "materials": linked("from", "materials"),
        "catalog": linked("for", "catalog"),
    }


def _prereq(ctx, work):
    """F5.1: each unmet condition carries its reason and the screen that fixes it."""
    experts = ctx["experts"]
    approved = [e for e in experts if e.get("evaluation_status") == "approved" or e.get("status") == "approved"]
    has_original = bool((work.get("body") or "").strip()) or any(
        (m.get("body") or "").strip() for m in ctx["materials"])
    checks = [
        {
            "key": "expert", "label": "승인 전문가", "ok": bool(approved),
            "reason": None if approved else (
                "연결된 전문가가 없습니다." if not experts else "연결된 전문가의 평가가 아직 승인되지 않았습니다."),
            "fix_link": "#s3", "fix_label": "S3 전문가 평가로 이동",
        },
        {
            "key": "original", "label": "원문", "ok": has_original,
            "reason": None if has_original else "작업 원문과 연결된 자료 원문이 모두 비어 있습니다.",
            "fix_link": "#s1", "fix_label": "S1 자료·원문으로 이동",
        },
        {
            "key": "catalog", "label": "카탈로그 연결", "ok": bool(ctx["catalog"]),
            "reason": None if ctx["catalog"] else "카탈로그에 연결되지 않았습니다.",
            "fix_link": "#s4", "fix_label": "S4 카탈로그 연결로 이동",
        },
    ]
    return {"ready": all(c["ok"] for c in checks), "checks": checks}


def _require_ready(ctx, work):
    prereq = _prereq(ctx, work)
    if not prereq["ready"]:
        raise _Fail(409, "prereq_unmet", "정제 전 조건이 충족되지 않았습니다.", checks=prereq["checks"])


def _refinement(raw):
    ref = raw.get("refinement")
    return ref if isinstance(ref, dict) else {}


def _input_hash(work, raw, ctx):
    """Review input identity: work title/body/evidence, accepted correction, its reason, linked records."""
    ref = _refinement(raw)
    payload = {
        "schema": "refine-input-v1",
        "work": {"id": work["id"], "title": work["title"], "body": work.get("body") or ""},
        "evidence": raw.get("evidence"),
        "corrected": ref.get("corrected"),
        "correction_reason": (ref.get("correction") or {}).get("reason"),
        "experts": sorted(
            [{"id": e["id"], "status": e["status"], "material_hash": e["material_hash"]} for e in ctx["experts"]],
            key=lambda x: x["id"]),
        "materials": sorted(
            [{"id": m["id"], "material_hash": m["material_hash"]} for m in ctx["materials"]],
            key=lambda x: x["id"]),
        "catalog": sorted(
            [{"id": c["id"], "material_hash": c["material_hash"]} for c in ctx["catalog"]],
            key=lambda x: x["id"]),
    }
    return hashlib.sha256(db.canonical_json(payload).encode("utf-8")).hexdigest()


def _review_rows(conn, work_id):
    return conn.execute(
        "SELECT * FROM refine_reviews WHERE work_id = ? ORDER BY seq DESC", (work_id,)).fetchall()


def _review_state(rows, corrected, input_hash):
    if not corrected:
        return "no_correction", None
    current = [r for r in rows if r["input_hash"] == input_hash]
    if current:
        return current[0]["decision"], current[0]["seq"]
    if rows:
        return "review_required", None
    return "pending", None


def _review_view(rows, input_hash):
    out = []
    for r in rows:
        out.append({
            "seq": r["seq"], "work_version": r["work_version"], "input_hash": r["input_hash"],
            "decision": r["decision"], "notes": r["notes"], "reviewer": r["reviewer"],
            "evidence_refs": json.loads(r["evidence_refs_json"]), "created_at": r["created_at"],
            "current_input": r["input_hash"] == input_hash,
        })
    return out


def _state_summary(conn, work):
    raw = _raw_fields(conn, work["id"])
    ctx = _context(conn, work["id"])
    input_hash = _input_hash(work, raw, ctx)
    corrected = _refinement(raw).get("corrected")
    state, _ = _review_state(_review_rows(conn, work["id"]), corrected, input_hash)
    return {"state": state, "prereq_ready": _prereq(ctx, work)["ready"]}


def _detail(conn, work_id):
    work = _work(conn, work_id)
    raw = _raw_fields(conn, work_id)
    ctx = _context(conn, work_id)
    ref = _refinement(raw)
    corrected = ref.get("corrected")
    input_hash = _input_hash(work, raw, ctx)
    rows = _review_rows(conn, work_id)
    state, _ = _review_state(rows, corrected, input_hash)
    revisions = conn.execute(
        "SELECT * FROM refine_revisions WHERE work_id = ? ORDER BY seq DESC", (work_id,)).fetchall()
    operator = db.get_metadata(conn, OPERATOR_KEY)
    return {
        "item": {
            "id": work["id"], "title": work["title"], "version": work["version"],
            "synthetic": work["synthetic"], "status": work["status"],
        },
        "prereq": _prereq(ctx, work),
        "views": {
            "original": {
                "label": "원문", "method": "source", "method_label": "사람 작성 원문",
                "title": work["title"], "body": work.get("body") or "",
                "materials": [{"id": m["id"], "title": m["title"], "body": m.get("body") or "",
                               "synthetic": m["synthetic"]} for m in ctx["materials"]],
            },
            "draft": {
                "label": "초안", "method": "rule", "method_label": "규칙/수동 생성 초안 (AI 아님)",
                "content": (ref.get("draft") or {}).get("content"),
                "generated_at": (ref.get("draft") or {}).get("generated_at"),
            },
            "ai_draft": _ai_view(ref.get("ai_draft")),
            "corrected": {
                "label": "교정본", "method": "human", "method_label": "사람 교정",
                "content": corrected,
                "reason": (ref.get("correction") or {}).get("reason"),
                "reviewer": (ref.get("correction") or {}).get("reviewer"),
                "at": (ref.get("correction") or {}).get("at"),
                "from_draft": (ref.get("correction") or {}).get("from_draft"),
                "from_ai_draft": (ref.get("correction") or {}).get("from_ai_draft"),
            },
            "evidence": {
                "label": "근거", "items": raw.get("evidence") or [],
                "materials": [{"id": m["id"], "title": m["title"]} for m in ctx["materials"]],
            },
        },
        "ai": ai.status("F10.2"),
        "ai_attempts": ref.get("ai_attempts") or [],
        "section_labels": SECTION_LABELS,
        "sections": SECTIONS,
        "input_hash": input_hash,
        "review": {
            "state": state,
            "operator": operator,
            "reviews": _review_view(rows, input_hash),
        },
        "history": ref.get("correction_history") or [],
        "revisions": [
            {"seq": r["seq"], "from_version": r["from_version"], "to_version": r["to_version"],
             "reason": r["reason"], "reviewer": r["reviewer"], "changed": json.loads(r["changed_json"]),
             "created_at": r["created_at"]}
            for r in revisions
        ],
    }


def _ai_view(draft):
    if not draft:
        return {"label": "AI 초안", "content": None, "status": None,
                "method_label": "AI 초안 (채택 전에는 참고용)"}
    return {"label": "AI 초안", "method_label": f"AI 초안 ({draft.get('model')})", **draft}


# ---------- routes ----------

@bp.get("/operator")
def get_operator():
    conn = _conn()
    try:
        return jsonify(operator=db.get_metadata(conn, OPERATOR_KEY))
    finally:
        conn.close()


@bp.post("/operator")
def set_operator():
    data = _json_body()
    name = _text(data, "name", 60, required=True)
    conn = _conn()
    try:
        with db.write_transaction(conn):
            db.set_metadata(conn, OPERATOR_KEY, name)
        return jsonify(operator=name)
    finally:
        conn.close()


@bp.get("/works")
def list_works():
    conn = _conn()
    try:
        items, total = db.list_records(conn, "works", limit=200)
        out = []
        for item in items:
            work = _work(conn, item["id"])
            out.append({**item, **_state_summary(conn, work)})
        return jsonify(items=out, total=total)
    finally:
        conn.close()


@bp.get("/works/<work_id>")
def get_work_detail(work_id):
    conn = _conn()
    try:
        return jsonify(_detail(conn, work_id))
    finally:
        conn.close()


@bp.post("/works/<work_id>/draft")
def make_draft(work_id):
    """F5.2: rule-based draft from the original and linked catalog. No AI call; a draft is not a review."""
    data = _json_body()
    expected = _expected_version(data)
    conn = _conn()
    try:
        with db.write_transaction(conn):
            work = _work(conn, work_id)
            if work["version"] != expected:
                raise _Fail(409, "version_conflict", "작업이 바뀌었습니다. 새로고침 후 다시 시도하세요.")
            ctx = _context(conn, work_id)
            _require_ready(ctx, work)
            raw = _raw_fields(conn, work_id)
            content = _rule_draft(work, ctx)
            ref = dict(_refinement(raw))
            ref["draft"] = {"content": content, "method": "rule", "generated_at": db.now_iso()}
            db.update_record(conn, "works", work_id, expected_version=expected,
                             changes={"fields": {**raw, "refinement": ref}})
        return jsonify(_detail(conn, work_id))
    finally:
        conn.close()


AI_SYSTEM = ("너는 현장 작업 기록을 정제하는 보조자다. 원문에 없는 사실은 만들지 않고, 모르는 단서는 "
             "'[추가 확인 필요]'로 남긴다. 아래 키를 가진 JSON 객체 하나만 답한다: "
             + ", ".join(SECTIONS) + " (각 값은 문자열).")


def _parse_ai_sections(text):
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    out = {}
    for key in SECTIONS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()[:MAX_SECTION_CHARS]
    return out or None


@bp.post("/works/<work_id>/ai-draft")
def request_ai_draft(work_id):
    """F10.2: ask the model for a refine draft. Stored apart from the original, the rule draft,
    the correction and any review; requesting/failing never changes approval state."""
    data = _json_body()
    expected = _expected_version(data)
    if not ai.adopted("F10.2"):
        raise _Fail(409, "ai_not_adopted", "AI 정제 초안이 꺼져 있습니다. 규칙 초안과 직접 교정을 사용하세요.")
    conn = _conn()
    try:
        with db.write_transaction(conn):
            work = _work(conn, work_id)
            if work["version"] != expected:
                raise _Fail(409, "version_conflict", "작업이 바뀌었습니다. 새로고침 후 다시 시도하세요.")
            ctx = _context(conn, work_id)
            _require_ready(ctx, work)
            raw = _raw_fields(conn, work_id)
            prompt = json.dumps({
                "title": work["title"], "body": (work.get("body") or "")[:MAX_BODY_CHARS],
                "materials": [(m.get("body") or "")[:4000] for m in ctx["materials"]],
                "rule_draft": _rule_draft(work, ctx)}, ensure_ascii=False)
            text, run = ai.attempt("F10.2", AI_SYSTEM, prompt, max_tokens=2000)
            ref = dict(_refinement(raw))
            parsed = _parse_ai_sections(text) if run.get("ok") else None
            if run.get("attempted") and run.get("ok") and parsed is None:
                run = {**run, "ok": False, "error": "unparsable_response"}
            attempt_run = {k: run.get(k) for k in ("provider", "model", "started_at", "finished_at",
                                                   "ok", "error", "usage", "input_hash")}
            ref["ai_attempts"] = (ref.get("ai_attempts") or [])[-(MAX_HISTORY - 1):] + [attempt_run]
            if parsed:
                ref["ai_draft"] = {"content": parsed, "status": "proposed", "mode": "ai",
                                   "model": run["model"], "provider": run.get("provider"),
                                   "started_at": run["started_at"], "finished_at": run["finished_at"],
                                   "input_hash": run["input_hash"], "usage": run.get("usage"),
                                   "based_on_work_version": work["version"]}
            db.update_record(conn, "works", work_id, expected_version=expected,
                             changes={"fields": {**raw, "refinement": ref}})
        out = _detail(conn, work_id)
        out["ai_result"] = {"ok": bool(parsed), "error": None if parsed else attempt_run["error"]}
        return jsonify(out)
    finally:
        conn.close()


def _ai_decision(work_id, status):
    data = _json_body()
    expected = _expected_version(data)
    reviewer = _text(data, "reviewer", 60, required=True)
    conn = _conn()
    try:
        with db.write_transaction(conn):
            work = _work(conn, work_id)
            if work["version"] != expected:
                raise _Fail(409, "version_conflict", "작업이 바뀌었습니다. 새로고침 후 다시 시도하세요.")
            raw = _raw_fields(conn, work_id)
            ref = dict(_refinement(raw))
            draft = ref.get("ai_draft")
            if not draft or draft.get("status") != "proposed":
                raise _Fail(409, "no_ai_draft", "채택 대기 중인 AI 초안이 없습니다.")
            ref["ai_draft"] = {**draft, "status": status, "decided_by": reviewer, "decided_at": db.now_iso()}
            db.update_record(conn, "works", work_id, expected_version=expected,
                             changes={"fields": {**raw, "refinement": ref}})
        return jsonify(_detail(conn, work_id))
    finally:
        conn.close()


@bp.post("/works/<work_id>/ai-draft/adopt")
def adopt_ai_draft(work_id):
    """Explicit human adoption. Adopting does not touch the correction or approval; it only allows
    editing the AI text into a correction (which then needs a new review)."""
    return _ai_decision(work_id, "adopted")


@bp.post("/works/<work_id>/ai-draft/reject")
def reject_ai_draft(work_id):
    return _ai_decision(work_id, "rejected")


def _rule_draft(work, ctx):
    body = (work.get("body") or "").strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", body) if s.strip()]
    cat = ctx["catalog"][0] if ctx["catalog"] else {}
    capability = cat.get("target_capability") or "목표 능력 미지정"
    domain = cat.get("domain") or "분야 미지정"
    observed = " / ".join(sentences[:3]) if sentences else "원문에 관찰 문장이 없습니다. 자료를 보완하세요."
    return {
        "context": f"{cat.get('title') or '카탈로그 미연결'} ({domain}) 과제의 '{work['title']}' 작업입니다. "
                   f"목표 능력: {capability}.",
        "observation": f"관찰 사실(원문 발췌): {observed}",
        "novice_errors": "[추가 확인 필요] 초보자가 자주 하는 오류는 전문가 설명 없이 추정하지 않습니다.",
        "judgement": "[추가 확인 필요] 판단 기준과 예외는 전문가가 채워야 합니다.",
        "outcome_check": "[추가 확인 필요] 결과를 확인하는 방법을 원문 근거와 함께 적으세요.",
        "criteria": f"[초안] '{capability}'를 원문 관찰 근거와 대조해 확인합니다.",
        "questions": "1. 원문에서 빠진 관찰 단서가 있나요?\n2. 예외 상황은 무엇인가요?",
    }


@bp.post("/works/<work_id>/correction")
def save_correction(work_id):
    """F5.4: human correction with a reason, saved as a new version. Earlier blocks are kept in history."""
    data = _json_body()
    expected = _expected_version(data)
    reason = _text(data, "correction_reason", MAX_REASON_CHARS, required=True)
    reviewer = _text(data, "reviewer", 60, required=True)
    from_draft = bool(data.get("from_draft"))
    from_ai_draft = bool(data.get("from_ai_draft"))
    raw_sections = data.get("refined")
    if not isinstance(raw_sections, dict):
        raise _Fail(400, "validation", "refined 객체를 보내 주세요.", fields=["refined"])
    refined = {}
    for key in SECTIONS:
        if key in raw_sections:
            text = raw_sections.get(key)
            if not isinstance(text, str):
                raise _Fail(400, "validation", f"{key} 값은 문자열이어야 합니다.", fields=[key])
            text = text.strip()
            if len(text) > MAX_SECTION_CHARS:
                raise _Fail(400, "validation", f"{key} 값이 너무 깁니다.", fields=[key])
            if text:
                refined[key] = text
    if not refined:
        raise _Fail(400, "validation", "교정 내용을 한 항목 이상 입력해 주세요.", fields=["refined"])
    conn = _conn()
    try:
        with db.write_transaction(conn):
            work = _work(conn, work_id)
            if work["version"] != expected:
                raise _Fail(409, "version_conflict", "작업이 바뀌었습니다. 새로고침 후 다시 시도하세요.")
            _require_ready(_context(conn, work_id), work)
            raw = _raw_fields(conn, work_id)
            ref = dict(_refinement(raw))
            ai_draft = ref.get("ai_draft") or {}
            if from_ai_draft and ai_draft.get("status") != "adopted":
                raise _Fail(409, "ai_draft_not_adopted", "AI 초안을 먼저 채택해야 교정의 바탕으로 쓸 수 있습니다.")
            history = list(ref.get("correction_history") or [])
            if ref.get("corrected"):
                history.append({"corrected": ref["corrected"], "correction": ref.get("correction")})
            ref["correction_history"] = history[-MAX_HISTORY:]
            ref["corrected"] = refined
            ref["correction"] = {"reason": reason, "reviewer": reviewer, "at": db.now_iso(),
                                 "from_draft": from_draft}
            if from_ai_draft:
                ref["correction"]["from_ai_draft"] = {"model": ai_draft.get("model"),
                                                       "input_hash": ai_draft.get("input_hash")}
            db.update_record(conn, "works", work_id, expected_version=expected,
                             changes={"fields": {**raw, "refinement": ref}})
        return jsonify(_detail(conn, work_id))
    finally:
        conn.close()


@bp.post("/works/<work_id>/validate")
def validate_work(work_id):
    """F5.5: designated operator approves or rejects the current input (version, hash, reason recorded)."""
    data = _json_body()
    expected = _expected_version(data)
    decision = data.get("decision")
    if decision not in ("approved", "rejected"):
        raise _Fail(400, "validation", "decision은 approved 또는 rejected여야 합니다.", fields=["decision"])
    notes = _text(data, "notes", MAX_REASON_CHARS, required=True)
    reviewer = _text(data, "reviewer", 60, required=True)
    input_hash = data.get("input_hash")
    if not isinstance(input_hash, str) or not input_hash:
        raise _Fail(400, "validation", "input_hash를 보내 주세요.", fields=["input_hash"])
    refs = data.get("evidence_refs") or []
    if not isinstance(refs, list) or len(refs) > 20 or not all(isinstance(r, str) and len(r) <= 200 for r in refs):
        raise _Fail(400, "validation", "evidence_refs는 문자열 목록이어야 합니다.", fields=["evidence_refs"])
    conn = _conn()
    try:
        with db.write_transaction(conn):
            operator = db.get_metadata(conn, OPERATOR_KEY)
            if not operator:
                raise _Fail(409, "operator_not_designated", "지정 운영책임자가 없습니다. 먼저 운영책임자를 지정하세요.")
            if reviewer != operator:
                raise _Fail(403, "not_designated_reviewer", "지정 운영책임자만 승인·반려를 기록할 수 있습니다.")
            work = _work(conn, work_id)
            if work["version"] != expected:
                raise _Fail(409, "version_conflict", "작업이 바뀌었습니다. 새로고침 후 다시 확인하세요.")
            raw = _raw_fields(conn, work_id)
            ctx = _context(conn, work_id)
            current = _input_hash(work, raw, ctx)
            if input_hash != current:
                raise _Fail(409, "stale_input", "검토 대상 입력이 바뀌었습니다. 현재 입력을 다시 확인하세요.",
                            current_input_hash=current)
            corrected = _refinement(raw).get("corrected")
            if not corrected:
                raise _Fail(409, "no_correction", "교정본이 없어 판정할 수 없습니다.")
            if decision == "approved":
                _require_ready(ctx, work)
            state, _ = _review_state(_review_rows(conn, work_id), corrected, current)
            if state == decision:
                raise _Fail(409, "already_recorded", "이 입력에 같은 판정이 이미 기록되어 있습니다.")
            conn.execute(
                "INSERT INTO refine_reviews (work_id, work_version, input_hash, decision, notes, reviewer, "
                "evidence_refs_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (work_id, work["version"], current, decision, notes, reviewer,
                 db.canonical_json(refs), db.now_iso()),
            )
        return jsonify(_detail(conn, work_id))
    finally:
        conn.close()


@bp.post("/works/<work_id>/revise")
def revise_original(work_id):
    """F5.6: revise the original text with a reason; the old text stays in record revisions."""
    data = _json_body()
    expected = _expected_version(data)
    reason = _text(data, "reason", MAX_REASON_CHARS, required=True)
    reviewer = _text(data, "reviewer", 60, required=True)
    changes = {}
    if "title" in data:
        changes["title"] = _text(data, "title", 120, required=True)
    if "body" in data:
        body = data.get("body")
        if not isinstance(body, str) or len(body) > MAX_BODY_CHARS:
            raise _Fail(400, "validation", "body 값이 올바르지 않습니다.", fields=["body"])
        changes["body"] = body.strip()
    if not changes:
        raise _Fail(400, "validation", "바꿀 원문(title 또는 body)을 보내 주세요.")
    conn = _conn()
    try:
        with db.write_transaction(conn):
            work = _work(conn, work_id)
            if work["version"] != expected:
                raise _Fail(409, "version_conflict", "작업이 바뀌었습니다. 새로고침 후 다시 시도하세요.")
            updated = db.update_record(conn, "works", work_id, expected_version=expected, changes=changes)
            if updated["version"] == expected:
                raise _Fail(400, "no_change", "바뀐 내용이 없습니다.")
            conn.execute(
                "INSERT INTO refine_revisions (work_id, from_version, to_version, reason, reviewer, "
                "changed_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (work_id, expected, updated["version"], reason, reviewer,
                 json.dumps(sorted(changes), ensure_ascii=False), db.now_iso()),
            )
        return jsonify(_detail(conn, work_id))
    finally:
        conn.close()
