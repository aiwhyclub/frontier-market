"""S3 전문가 후보·평가·판정·원시 작업 업로드 (EPIC 4, F3.1~F3.6, TRD §8.2~8.3).

- 전문가는 experts 레코드, 업로드된 작업은 works 레코드(status=uploaded_unlinked, catalog 링크 없음).
- 점수는 사람이 4축(0~3 정수)으로 입력하고 서버가 합계·가중치를 계산한다. 점수만으로 판정이 바뀌지 않는다.
- 판정(approved/supplement/rejected)은 지정 운영책임자가 명시적으로 기록할 때만 status를 바꾼다.
- AI 초안(F10.3): 모집문·경험 정리·평가 질문은 채택된 경우에만 요청할 수 있고 fields.ai_draft 에 편집 가능한
  "초안"으로만 저장한다. 모든 시도는 fields.ai_attempts 에 남기며, 실제 모집 연락·전문가 승인으로 표시하지 않고
  판정·상태는 바꾸지 않는다.
- 프로필 수정은 record_revisions에 이전 내용이 남고, 경험·도제 맥락·제공 범위가 바뀌면 재평가 필요 표시를 남긴다.
"""

import json
from contextlib import closing

from flask import Blueprint, current_app, jsonify, request

from backend import ai, db, i18n

bp = Blueprint("experts", __name__, url_prefix="/api/experts")

AXES = ("apprenticeship", "signals", "judgment", "evidence")
AXIS_LABELS = {
    "apprenticeship": "도제 경험",
    "signals": "관측 단서",
    "judgment": "판단/예외",
    "evidence": "결과/근거",
}
RUBRIC_VERSION = "expert-v1"
WEIGHT = 0.25
DECISIONS = {"approved": "승인", "supplement": "보완", "rejected": "반려"}
REEVAL_TRIGGER_KEYS = ("experience", "apprenticeship", "provided_scope")
PROFILE_KEYS = {"name", "field", "experience", "apprenticeship", "participation_notes", "provided_scope"}
MAX_SHORT = 120
MAX_TEXT = 2000
MAX_RAW = 20000


def _error(status, code, message, **extra):
    return jsonify(error={"code": code, "message": message, **extra}), status


def _conn():
    return db.connect(current_app.config["DB_PATH"])


def _json_object():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _check_text(data, key, errors, *, required, max_len):
    if key not in data or data[key] is None:
        if required:
            errors[key] = "필수 항목입니다."
        return None
    value = data[key]
    if not isinstance(value, str):
        errors[key] = "문자열이어야 합니다."
        return None
    value = value.strip()
    if required and not value:
        errors[key] = "필수 항목입니다."
        return None
    if len(value) > max_len:
        errors[key] = f"{max_len}자 이하로 입력해 주세요."
        return None
    return value


def _check_version(data, errors):
    version = data.get("expected_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        errors["expected_version"] = "현재 버전 번호가 필요합니다."
        return None
    return version


def _check_scores(raw, errors):
    if not isinstance(raw, dict):
        errors["scores"] = "4축 점수 객체가 필요합니다."
        return None
    extra = set(raw) - set(AXES)
    if extra:
        errors["scores"] = f"허용되지 않은 점수 항목: {', '.join(sorted(extra))}. 합계는 서버가 계산합니다."
        return None
    scores = {}
    for axis in AXES:
        value = raw.get(axis)
        if value is None:
            errors[f"scores.{axis}"] = f"{AXIS_LABELS[axis]} 점수를 입력해 주세요. 빈 값은 0점과 다릅니다."
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3:
            errors[f"scores.{axis}"] = f"{AXIS_LABELS[axis]} 점수는 0~3 정수여야 합니다."
            continue
        scores[axis] = value
    return scores if len(scores) == len(AXES) else None


def _expert_or_404(conn, expert_id):
    item = db.get_record(conn, "experts", expert_id)
    if item is None:
        return None, _error(404, "not_found", "전문가 후보를 찾을 수 없습니다.")
    return item, None


def _stored_fields(conn, expert_id):
    row = conn.execute("SELECT fields_json FROM records WHERE type = 'experts' AND id = ?",
                       (expert_id,)).fetchone()
    return json.loads(row["fields_json"]) if row else {}


def _works_of(conn, expert_id):
    rows = conn.execute(
        "SELECT id, title, status, synthetic, version FROM records "
        "WHERE type = 'works' AND json_extract(fields_json, '$.expert_id') = ? ORDER BY created_at",
        (expert_id,),
    ).fetchall()
    return [{**dict(r), "synthetic": bool(r["synthetic"])} for r in rows]


def _next_action(status, reeval):
    if reeval:
        return "경험 또는 제공 범위가 바뀌었습니다. 현재 평가와 연결된 작업을 다시 확인한 뒤 평가를 기록하세요."
    return {
        "candidate": "4축 점수와 판정(승인/보완/반려)을 지정 운영책임자가 기록하세요. 점수만으로는 승인되지 않습니다.",
        "supplement": "보완 사유를 확인하고 필요한 경험·근거를 보완한 뒤 다시 평가하세요.",
        "approved": "승인된 전문가입니다. 원시 작업을 업로드할 수 있습니다.",
        "rejected": "반려 사유를 확인하세요. 필요하면 다시 평가를 기록할 수 있습니다. 원시 작업은 업로드할 수 없습니다.",
    }.get(status, "현재 판정을 확인하세요.")


def _detail_payload(conn, item):
    stored = _stored_fields(conn, item["id"])
    evaluations = list(stored.get("evaluations") or [])
    revisions = conn.execute(
        "SELECT version, created_at FROM record_revisions WHERE type = 'experts' AND id = ? ORDER BY version",
        (item["id"],),
    ).fetchall()
    reeval = bool(item.get("reevaluation_required"))
    return {
        "item": item,
        "current_verdict": {
            "status": item["status"],
            "label": DECISIONS.get(item["status"], "지원(후보)" if item["status"] == "candidate" else item["status"]),
            "evaluation": stored.get("evaluation"),
        },
        "past_evaluations": list(reversed(evaluations)),
        "works": _works_of(conn, item["id"]),
        "upload_allowed": item["status"] == "approved",
        "reevaluation_required": reeval,
        "next_action": _next_action(item["status"], reeval),
        "revisions": [{"version": r["version"], "created_at": r["created_at"]} for r in revisions],
        "ai": ai.status("F10.3"),
        "ai_draft": _ai_draft_view(stored.get("ai_draft")),
        "ai_attempts": list(stored.get("ai_attempts") or []),
    }


@bp.post("")
def create_candidate():
    data = _json_object()
    if data is None:
        return _error(400, "invalid_json", "JSON 객체를 보내 주세요.")
    errors = {}
    name = _check_text(data, "name", errors, required=True, max_len=MAX_SHORT)
    field = _check_text(data, "field", errors, required=True, max_len=MAX_SHORT)
    experience = _check_text(data, "experience", errors, required=True, max_len=MAX_TEXT)
    apprenticeship = _check_text(data, "apprenticeship", errors, required=True, max_len=MAX_TEXT)
    notes = _check_text(data, "participation_notes", errors, required=False, max_len=MAX_TEXT)
    scope = _check_text(data, "provided_scope", errors, required=False, max_len=MAX_TEXT)
    synthetic = data.get("synthetic", False)
    if not isinstance(synthetic, bool):
        errors["synthetic"] = "true 또는 false여야 합니다."
    if errors:
        return _error(400, "validation", "입력을 확인해 주세요.", fields=errors)
    fields = {
        "field": field, "experience": experience, "apprenticeship": apprenticeship,
        "participation_notes": notes or "", "provided_scope": scope or "",
        "evaluation_status": "candidate", "evaluation": None, "evaluations": [],
        "reevaluation_required": False, "reevaluation_reason": None,
    }
    try:
        idem = db.normalize_idempotency_key(request.headers.get("Idempotency-Key"))
    except ValueError as exc:
        return _error(400, "validation", str(exc), fields={"Idempotency-Key": str(exc)})
    try:
        with closing(_conn()) as conn, db.write_transaction(conn):
            prior = db.replay_idempotent(conn, "experts.create", idem, data) if idem else None
            if prior:
                rid = prior[1]
            else:
                rid = db.create_record(conn, "experts", title=name, status="candidate",
                                       synthetic=synthetic, fields=fields)
                if idem:
                    db.remember_idempotent(conn, "experts.create", idem, data, "experts", rid)
            item = db.get_record(conn, "experts", rid)
    except db.IdempotencyConflict:
        return _error(409, "idempotency_conflict",
                      "같은 요청 번호로 다른 내용이 이미 저장되었습니다. 새로 작성해 주세요.")
    return jsonify(item=item), 201


@bp.get("/<expert_id>")
def expert_detail(expert_id):
    with closing(_conn()) as conn:
        item, err = _expert_or_404(conn, expert_id)
        if err:
            return err
        return jsonify(_detail_payload(conn, item))


@bp.patch("/<expert_id>")
def edit_profile(expert_id):
    data = _json_object()
    if data is None:
        return _error(400, "invalid_json", "JSON 객체를 보내 주세요.")
    unknown = set(data) - PROFILE_KEYS - {"expected_version"}
    if unknown:
        return _error(400, "not_updatable", f"수정할 수 없는 항목입니다: {', '.join(sorted(unknown))}",
                      fields={k: "허용되지 않은 항목입니다." for k in unknown})
    errors = {}
    version = _check_version(data, errors)
    texts = {}
    for key in PROFILE_KEYS:
        if key in data:
            texts[key] = _check_text(data, key, errors, required=key in ("name", "field", "experience", "apprenticeship"),
                                     max_len=MAX_SHORT if key in ("name", "field") else MAX_TEXT)
    if errors:
        return _error(400, "validation", "입력을 확인해 주세요.", fields=errors)
    with closing(_conn()) as conn:
        try:
            with db.write_transaction(conn):
                item, err = _expert_or_404(conn, expert_id)
                if err:
                    return err
                stored = _stored_fields(conn, expert_id)
                new_fields = dict(stored)
                changed_trigger = []
                for key in ("field", "experience", "apprenticeship", "participation_notes", "provided_scope"):
                    if key in texts and texts[key] is not None:
                        if texts[key] != stored.get(key):
                            if key in REEVAL_TRIGGER_KEYS:
                                changed_trigger.append(key)
                        new_fields[key] = texts[key]
                changes = {"fields": new_fields}
                if texts.get("name") and texts["name"] != item["title"]:
                    changes["title"] = texts["name"]
                if changed_trigger and item["status"] in ("approved", "supplement", "rejected", "candidate"):
                    new_fields["reevaluation_required"] = True
                    new_fields["reevaluation_reason"] = i18n.tr("경험·도제 맥락·제공 범위가 바뀌었습니다: {}", ", ".join(changed_trigger))
                db.update_record(conn, "experts", expert_id, expected_version=version, changes=changes)
                item = db.get_record(conn, "experts", expert_id)
                payload = _detail_payload(conn, item)
        except db.RecordConflict:
            return _error(409, "version_conflict", "다른 곳에서 먼저 수정되었습니다. 새로고침 후 다시 시도해 주세요.")
    return jsonify(payload)


@bp.post("/<expert_id>/evaluate")
def evaluate(expert_id):
    data = _json_object()
    if data is None:
        return _error(400, "invalid_json", "JSON 객체를 보내 주세요.")
    errors = {}
    version = _check_version(data, errors)
    if "score" in data:
        errors["score"] = "합계는 서버가 계산합니다. 보내지 마세요."
    scores = _check_scores(data.get("scores"), errors)
    decision = data.get("decision")
    if decision not in DECISIONS:
        errors["decision"] = "판정은 approved(승인), supplement(보완), rejected(반려) 중 하나여야 합니다."
    notes = _check_text(data, "notes", errors, required=True, max_len=MAX_TEXT)
    reviewer = _check_text(data, "reviewer", errors, required=True, max_len=MAX_SHORT)
    refs = data.get("evidence_refs", [])
    if not isinstance(refs, list) or len(refs) > 20 or not all(isinstance(r, str) and len(r) <= 200 for r in refs):
        errors["evidence_refs"] = "근거 참조는 200자 이하 문자열 목록(최대 20개)이어야 합니다."
    if errors:
        return _error(400, "validation", "입력을 확인해 주세요.", fields=errors)
    total = sum(scores[a] for a in AXES)
    with closing(_conn()) as conn:
        try:
            with db.write_transaction(conn):
                item, err = _expert_or_404(conn, expert_id)
                if err:
                    return err
                evaluation = {
                    "rubric_version": RUBRIC_VERSION,
                    "scores": {a: scores[a] for a in AXES},
                    "weights": {a: WEIGHT for a in AXES},
                    "score": total,
                    "decision": decision,
                    "notes": notes,
                    "reviewer": reviewer,
                    "evidence_refs": refs,
                    "evaluated_at": db.now_iso(),
                }
                new_fields = _stored_fields(conn, expert_id)
                new_fields["evaluations"] = list(new_fields.get("evaluations") or []) + [evaluation]
                new_fields["evaluation"] = evaluation
                new_fields["evaluation_status"] = decision
                new_fields["reevaluation_required"] = False
                new_fields["reevaluation_reason"] = None
                db.update_record(conn, "experts", expert_id, expected_version=version,
                                 changes={"status": decision, "fields": new_fields})
                item = db.get_record(conn, "experts", expert_id)
                payload = _detail_payload(conn, item)
        except db.RecordConflict:
            return _error(409, "version_conflict", "다른 곳에서 먼저 수정되었습니다. 새로고침 후 다시 시도해 주세요.")
    return jsonify(payload), 201


@bp.post("/<expert_id>/uploads")
def upload_work(expert_id):
    data = _json_object()
    if data is None:
        return _error(400, "invalid_json", "JSON 객체를 보내 주세요.")
    errors = {}
    title = _check_text(data, "title", errors, required=True, max_len=MAX_SHORT)
    raw_text = _check_text(data, "raw_text", errors, required=True, max_len=MAX_RAW)
    evidence = _check_text(data, "evidence", errors, required=False, max_len=MAX_TEXT)
    synthetic = data.get("synthetic", False)
    if not isinstance(synthetic, bool):
        errors["synthetic"] = "true 또는 false여야 합니다."
    if data.get("catalog_id") not in (None, ""):
        errors["catalog_id"] = "업로드 단계에서는 카탈로그를 연결할 수 없습니다. S4에서 나중에 연결하세요."
    source_ids = data.get("source_ids", [])
    if not isinstance(source_ids, list) or len(source_ids) > 20 or not all(
            isinstance(s, str) and s for s in source_ids):
        errors["source_ids"] = "자료 ID 목록(최대 20개)이어야 합니다."
    if errors:
        return _error(400, "validation", "입력을 확인해 주세요.", fields=errors)
    with closing(_conn()) as conn:
        with db.write_transaction(conn):
            item, err = _expert_or_404(conn, expert_id)
            if err:
                return err
            if item["status"] != "approved":
                return _error(403, "expert_not_approved",
                              "승인된 전문가만 원시 작업을 업로드할 수 있습니다. 먼저 평가에서 승인 판정을 기록하세요.",
                              current_verdict=item["status"])
            for sid in source_ids:
                if db.get_record(conn, "materials", sid) is None:
                    return _error(400, "validation", "연결할 자료를 찾을 수 없습니다.",
                                  fields={"source_ids": f"자료 없음: {sid}"})
            fields = {"expert_id": expert_id, "evidence": evidence or "", "source_ids": list(source_ids),
                      "catalog_id": None}
            wid = db.create_record(conn, "works", title=title, status="uploaded_unlinked",
                                   synthetic=synthetic, body=raw_text, fields=fields)
            db.add_link(conn, "works", wid, "experts", expert_id, "by")
            for sid in source_ids:
                db.add_link(conn, "works", wid, "materials", sid, "from")
            work = db.get_record(conn, "works", wid)
    return jsonify(
        item=work,
        guidance="카탈로그가 아직 없어 미연결(uploaded_unlinked) 상태로 저장했습니다. "
                 "S4 카탈로그를 만든 뒤 그 화면에서 이 작업을 연결하세요.",
    ), 201


# ---------------------------------------------------------------- F10.3 AI drafts

AI_DRAFT_KEYS = {"recruiting_post": "모집문 초안", "experience_summary": "경험 정리 초안",
                 "evaluation_questions": "평가 질문 초안"}
AI_NOTICE = ("AI가 만든 초안입니다. 실제 모집 연락을 보낸 것이 아니고 전문가 승인도 아닙니다. "
             "수정한 뒤 사람이 직접 사용하세요.")
AI_SYSTEM = ("너는 전문가 후보 모집을 돕는 보조자다. 주어진 프로필에 없는 경력은 만들지 않는다. "
             'JSON 객체 하나만 답한다: {"recruiting_post": 문자열, "experience_summary": 문자열, '
             '"evaluation_questions": 문자열 배열(최대 8개)}')


def _ai_draft_view(draft):
    if not draft:
        return None
    return {**draft, "label": f"AI 초안 ({draft.get('model')})", "notice": AI_NOTICE,
            "outreach_sent": False, "expert_approval": False}


def _parse_ai_draft(text):
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    post, summary, questions = (data.get("recruiting_post"), data.get("experience_summary"),
                                data.get("evaluation_questions"))
    if not (isinstance(post, str) and post.strip() and isinstance(summary, str) and summary.strip()):
        return None
    if not (isinstance(questions, list) and questions
            and all(isinstance(q, str) and q.strip() for q in questions)):
        return None
    return {"recruiting_post": post.strip()[:MAX_TEXT], "experience_summary": summary.strip()[:MAX_TEXT],
            "evaluation_questions": [q.strip()[:300] for q in questions[:8]]}


@bp.post("/<expert_id>/ai-draft")
def request_ai_draft(expert_id):
    data = _json_object()
    if data is None:
        return _error(400, "invalid_json", "JSON 객체를 보내 주세요.")
    errors = {}
    version = _check_version(data, errors)
    if errors:
        return _error(400, "validation", "입력을 확인해 주세요.", fields=errors)
    if not ai.adopted("F10.3"):
        return _error(409, "ai_not_adopted", "AI 초안 기능이 꺼져 있습니다. 모집문과 질문은 직접 작성하세요.")
    with closing(_conn()) as conn:
        try:
            with db.write_transaction(conn):
                item, err = _expert_or_404(conn, expert_id)
                if err:
                    return err
                stored = _stored_fields(conn, expert_id)
                profile = {k: stored.get(k) for k in ("field", "experience", "apprenticeship",
                                                      "participation_notes", "provided_scope")}
                profile["name"] = item["title"]
                text, run = ai.attempt("F10.3", AI_SYSTEM, json.dumps(profile, ensure_ascii=False))
                parsed = _parse_ai_draft(text) if run.get("ok") else None
                if run.get("attempted") and run.get("ok") and parsed is None:
                    run = {**run, "ok": False, "error": "unparsable_response"}
                attempt_run = {k: run.get(k) for k in ("provider", "model", "started_at", "finished_at",
                                                       "ok", "error", "usage", "input_hash")}
                new_fields = dict(stored)
                new_fields["ai_attempts"] = (stored.get("ai_attempts") or [])[-49:] + [attempt_run]
                if parsed:
                    new_fields["ai_draft"] = {"status": "draft", "content": parsed, "model": run["model"],
                                              "started_at": run["started_at"], "finished_at": run["finished_at"],
                                              "input_hash": run["input_hash"], "usage": run.get("usage"),
                                              "edited_at": None}
                db.update_record(conn, "experts", expert_id, expected_version=version,
                                 changes={"fields": new_fields})
                payload = _detail_payload(conn, db.get_record(conn, "experts", expert_id))
        except db.RecordConflict:
            return _error(409, "version_conflict", "다른 곳에서 먼저 수정되었습니다. 새로고침 후 다시 시도해 주세요.")
    payload["ai_result"] = {"ok": bool(parsed), "error": None if parsed else attempt_run["error"]}
    return jsonify(payload)


@bp.patch("/<expert_id>/ai-draft")
def edit_ai_draft(expert_id):
    """Edit the draft text. Still a draft: nothing is sent and the verdict is untouched."""
    data = _json_object()
    if data is None:
        return _error(400, "invalid_json", "JSON 객체를 보내 주세요.")
    errors = {}
    version = _check_version(data, errors)
    post = _check_text(data, "recruiting_post", errors, required=True, max_len=MAX_TEXT)
    summary = _check_text(data, "experience_summary", errors, required=True, max_len=MAX_TEXT)
    questions = data.get("evaluation_questions")
    if not (isinstance(questions, list) and questions
            and all(isinstance(q, str) and q.strip() and len(q) <= 300 for q in questions)):
        errors["evaluation_questions"] = "비어 있지 않은 문자열 목록이어야 합니다(각 300자 이하)."
    if errors:
        return _error(400, "validation", "입력을 확인해 주세요.", fields=errors)
    with closing(_conn()) as conn:
        try:
            with db.write_transaction(conn):
                item, err = _expert_or_404(conn, expert_id)
                if err:
                    return err
                stored = _stored_fields(conn, expert_id)
                draft = stored.get("ai_draft")
                if not draft:
                    return _error(404, "no_ai_draft", "수정할 AI 초안이 없습니다.")
                new_fields = dict(stored)
                new_fields["ai_draft"] = {**draft, "status": "edited", "edited_at": db.now_iso(),
                                          "content": {"recruiting_post": post, "experience_summary": summary,
                                                      "evaluation_questions": [q.strip() for q in questions[:8]]}}
                db.update_record(conn, "experts", expert_id, expected_version=version,
                                 changes={"fields": new_fields})
                payload = _detail_payload(conn, db.get_record(conn, "experts", expert_id))
        except db.RecordConflict:
            return _error(409, "version_conflict", "다른 곳에서 먼저 수정되었습니다. 새로고침 후 다시 시도해 주세요.")
    return jsonify(payload)
