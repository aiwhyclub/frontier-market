"""S7 virtual company (EPIC 9): gates, frozen package, samples, per-task runs, JSON export.

Responses: {"error": {"code", "message"}} on failure. Gate HOLDs are returned as 409 with "holds";
prerequisite problems as 422 with "errors". Nothing is saved when a gate blocks.
"""

import hashlib
import json
from contextlib import closing

from flask import Blueprint, Response, current_app, jsonify, request

from backend import db
from backend.features import aidraft

bp = Blueprint("company", __name__, url_prefix="/api/company")

PURPOSES = ("training", "evaluation")
ACTIVE_RIGHT_STATUSES = {"confirmed", "learning_allowed", "eval_only", "restricted"}
RUN_ACTIONS = ("request_inspection", "hold", "release")
CRITERIA = [
    {"id": "c1", "label": "절차 순서 확인"},
    {"id": "c2", "label": "근거 연결"},
    {"id": "c3", "label": "위험 판단 설명"},
    {"id": "c4", "label": "검토자 승인 확인"},
]

SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS company_packages (
        id TEXT PRIMARY KEY, company_id TEXT NOT NULL, purpose TEXT NOT NULL,
        snapshot_text TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS company_runs (
        company_id TEXT NOT NULL, task_id TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,
        state TEXT NOT NULL, inspection_complete INTEGER NOT NULL DEFAULT 0, score INTEGER,
        run_version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (company_id, task_id))""",
    """CREATE TABLE IF NOT EXISTS company_run_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT NOT NULL, task_id TEXT NOT NULL,
        attempt INTEGER NOT NULL, event TEXT NOT NULL, from_state TEXT, to_state TEXT,
        score INTEGER, detail_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS company_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, subject_type TEXT NOT NULL,
        subject_id TEXT NOT NULL, subject_version INTEGER, reason TEXT NOT NULL,
        evidence TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL)""",
]


def init_schema(conn):
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
    aidraft.init_schema(conn)


def _error(status, code, message, **extra):
    return jsonify(error={"code": code, "message": message}, **extra), status


def _conn():
    conn = db.connect(current_app.config["DB_PATH"])
    init_schema(conn)
    return conn


def _now():
    return db.now_iso()


def _norm_purpose(value):
    return {"learning": "training"}.get(value, value)


def _rights_for(conn, subject_type, subject_id):
    ids = {r["from_id"] for r in conn.execute(
        "SELECT from_id FROM record_links WHERE from_type='rights' AND to_type=? AND to_id=? "
        "AND relation='subject'", (subject_type, subject_id)).fetchall()}
    for r in conn.execute("SELECT id FROM records WHERE type='rights'").fetchall():
        rec = db.get_record(conn, "rights", r["id"])
        if rec.get("target_type") == subject_type and rec.get("target_id") == subject_id:
            ids.add(r["id"])
    return [db.get_record(conn, "rights", i) for i in sorted(ids)]


def _scope_of(rec, now):
    status = rec.get("status")
    if status not in ACTIVE_RIGHT_STATUSES:
        return None, f"상태 {status}"
    expires = rec.get("valid_until") or rec.get("expires_at")
    if expires and str(expires) < now[:10]:
        return None, "기간 만료"
    if status == "eval_only":
        purposes = {"evaluation"}
    else:
        purposes = {_norm_purpose(p) for p in (rec.get("purposes") or [])}
        if not purposes and status == "learning_allowed":
            purposes = {"training", "evaluation"}
    return {"record_id": rec["id"], "record_version": rec["version"], "status": status,
            "purposes": sorted(purposes)}, None


def _subject_hold(conn, subject_type, subject_id, purpose, now, pinned_hash=None):
    """Return a HOLD dict for one subject and purpose, or None when the scope covers it.

    Grants bound to another content hash (H) are past content and are ignored. Every other
    grant for the same content must be active: a revoked, unconfirmed or expired grant holds.
    Unbound legacy grants (no H) are kept as before.
    """
    rec = db.get_record(conn, subject_type, subject_id)
    title = rec.get("title") if rec else subject_id
    current_hash = pinned_hash or (rec.get("material_hash") if rec else None)

    def hold(reason):
        return {"target_type": subject_type, "target_id": subject_id, "title": title,
                "purpose": purpose, "reason": reason, "action": "rights_supplement"}

    scopes, blocking = [], []
    for right in _rights_for(conn, subject_type, subject_id):
        bound = right.get("subject_material_hash")
        if bound and current_hash and bound != current_hash:
            continue  # grant for another content version (H): not applicable to this content
        scope, why = _scope_of(right, now)
        if scope is None:
            blocking.append(f"{right['id']}: {why}")
        else:
            scopes.append(scope)
    if blocking:
        return hold(f"현재 내용에 대한 권리가 유효하지 않습니다 ({'; '.join(blocking)}).")
    if not scopes:
        return hold("권리 기록이 없거나 현재 내용에 대해 미확인입니다.")
    allowed = set.intersection(*(set(s["purposes"]) for s in scopes))
    if purpose in allowed:
        return None
    if purpose == "training" and "evaluation" in allowed:
        return hold("평가 전용 범위입니다. 평가 전용 자료는 학습 허용으로 바뀌지 않습니다.")
    return hold(f"'{purpose}' 목적의 이용 범위가 부족합니다.")


def _revocations(conn, subject_type, subject_id, version):
    return conn.execute(
        "SELECT * FROM company_events WHERE kind='revoke' AND subject_type=? AND subject_id=? "
        "AND subject_version=?", (subject_type, subject_id, version)).fetchall()


def _work_verified(work):
    # EPIC 7 writes review_status=approved on the work; seed works are accepted as-is.
    return work.get("review_status") == "approved" or str(work["id"]).startswith("wrk_seed")


def _expert_approved(expert):
    return expert.get("status") == "approved" or expert.get("evaluation_status") == "approved"


def _linked(conn, record_type, record_id, relation, other_type):
    links, _missing = db.relations(conn, record_type, record_id)
    return [l for l in links if l["relation"] == relation and l["type"] == other_type
            and l["direction"] == "out"]


def _creation_gate(conn, body, now):
    errors, holds = [], []
    name = str(body.get("name") or "").strip()
    purpose = body.get("purpose")
    work_ids = body.get("work_ids")
    catalog_id = body.get("catalog_id")
    project_id = body.get("project_id")
    if not name or len(name) > 120:
        errors.append({"field": "name", "message": "회사 이름을 1~120자로 입력하세요."})
    if purpose not in PURPOSES:
        errors.append({"field": "purpose", "message": "목적은 학습(training) 또는 평가(evaluation)입니다."})
    if not isinstance(work_ids, list) or not work_ids:
        errors.append({"field": "work_ids", "message": "검증된 작업을 1개 이상 선택하세요."})
        work_ids = []
    catalog = db.get_record(conn, "catalog", catalog_id) if isinstance(catalog_id, str) else None
    if catalog is None:
        errors.append({"field": "catalog_id", "message": "카탈로그를 선택하세요."})
    project = db.get_record(conn, "projects", project_id) if isinstance(project_id, str) else None
    if project is None:
        errors.append({"field": "project_id", "message": "필수 프로젝트를 선택하세요."})
    elif catalog is not None and not any(
            l["id"] == catalog_id for l in _linked(conn, "projects", project_id, "catalog", "catalog")):
        errors.append({"field": "project_id", "message": "프로젝트와 카탈로그 관계가 일치하지 않습니다."})

    works, sources, experts = [], {}, {}
    for wid in work_ids:
        work = db.get_record(conn, "works", wid) if isinstance(wid, str) else None
        if work is None:
            errors.append({"field": "work_ids", "message": f"작업을 찾을 수 없습니다: {wid}"})
            continue
        if catalog is not None and not any(
                l["id"] == catalog_id for l in _linked(conn, "works", wid, "for", "catalog")):
            errors.append({"field": "work_ids", "message": f"작업과 카탈로그 관계가 없습니다: {work['title']}"})
        if not _work_verified(work):
            errors.append({"field": "work_ids", "message": f"사람 검증(승인)되지 않은 작업입니다: {work['title']}"})
        if _revocations(conn, "works", wid, work["version"]):
            holds.append({"target_type": "works", "target_id": wid, "title": work["title"],
                          "purpose": purpose, "reason": "이 버전의 승인이 취소되었습니다.",
                          "action": "review_required"})
        for link in _linked(conn, "works", wid, "by", "experts"):
            expert = db.get_record(conn, "experts", link["id"])
            if expert is None or not _expert_approved(expert):
                errors.append({"field": "work_ids", "message": f"승인되지 않은 전문가의 작업입니다: {work['title']}"})
            else:
                experts[expert["id"]] = expert
        for link in _linked(conn, "works", wid, "from", "materials"):
            sources[link["id"]] = db.get_record(conn, "materials", link["id"])
        lp = work.get("license_purpose")
        if purpose in PURPOSES and lp in PURPOSES and lp != purpose:
            holds.append({"target_type": "works", "target_id": wid, "title": work["title"],
                          "purpose": purpose, "reason": f"작업 제한은 '{lp}'입니다.",
                          "action": "rights_supplement"})
        works.append(work)

    if purpose in PURPOSES and not errors:
        subjects = [("works", w) for w in works] + [("materials", m) for m in sources.values()] \
            + [("experts", e) for e in experts.values()]
        for stype, rec in subjects:
            hold = _subject_hold(conn, stype, rec["id"], purpose, now)
            if hold:
                holds.append(hold)
    return {"name": name, "purpose": purpose, "errors": errors, "holds": holds, "works": works,
            "sources": list(sources.values()), "experts": list(experts.values()),
            "catalog": catalog, "project": project, "catalog_id": catalog_id,
            "project_id": project_id}


def _export_gate(conn, pkg, now):
    """Recheck the frozen package's pinned provenance against current rights and revocations."""
    purpose = pkg["company"]["purpose"]
    holds = []
    for item in pkg["provenance"]:
        stype, sid, version = item["type"], item["id"], item["version"]
        for rev in _revocations(conn, stype, sid, version):
            holds.append({"target_type": stype, "target_id": sid, "title": sid, "purpose": purpose,
                          "reason": f"과거 승인 취소(사건 {rev['id']}): {rev['reason']}",
                          "action": "approval_revoked"})
        if stype == "works":
            work = db.get_record(conn, "works", sid)
            if work is None or not _work_verified(work):
                holds.append({"target_type": stype, "target_id": sid, "title": sid, "purpose": purpose,
                              "reason": "현재 검토 상태가 승인이 아닙니다.", "action": "review_required"})
        if stype == "experts":
            expert = db.get_record(conn, "experts", sid)
            if expert is None or not _expert_approved(expert):
                holds.append({"target_type": stype, "target_id": sid, "title": sid, "purpose": purpose,
                              "reason": "전문가 승인 상태를 현재 확인할 수 없습니다.", "action": "review_required"})
        hold = _subject_hold(conn, stype, sid, purpose, now, pinned_hash=item.get("material_hash"))
        if hold:
            holds.append(hold)
    return {"allowed": not holds, "checked_at": now, "holds": holds}


def _build_package(conn, company_id, gate, now):
    purpose = gate["purpose"]
    works = gate["works"]
    training, evaluation, documents, tasks, answers = [], [], [], [], []
    provenance, rights = [], []
    for work in works:
        task_id = f"tsk_{work['id']}"
        tasks.append({"task_id": task_id, "work_id": work["id"], "title": work["title"],
                      "criteria": [c["id"] for c in CRITERIA]})
        answers.append({"task_id": task_id, "answer": work.get("body") or ""})
        documents.append({"id": work["id"], "title": work["title"], "body": work.get("body") or ""})
        provenance.append({"type": "works", "id": work["id"], "version": work["version"],
                           "material_hash": work["material_hash"]})
        if purpose == "training":
            training.append({
                "input": {"work_id": work["id"], "text": work.get("body") or ""},
                "review_output": {"summary": "합성 검토 출력(가상)", "status": "approved"},
                "evidence": [{"material_id": m["id"], "title": m["title"]} for m in gate["sources"]],
            })
        else:
            evaluation.append({
                "prompt": f"가상 과제: {work['title']}",
                "allowed_outcome": "기준에 맞게 판단 순서와 근거를 설명",
                "criteria_ref": [c["id"] for c in CRITERIA],
                "exposure": "hidden",
            })
    for m in gate["sources"]:
        provenance.append({"type": "materials", "id": m["id"], "version": m["version"],
                           "material_hash": m["material_hash"]})
    for e in gate["experts"]:
        provenance.append({"type": "experts", "id": e["id"], "version": e["version"],
                           "material_hash": e["material_hash"]})
    seen = set()
    for stype, rec in ([("works", w) for w in works] + [("materials", m) for m in gate["sources"]]
                       + [("experts", e) for e in gate["experts"]]):
        if (stype, rec["id"]) in seen:
            continue
        seen.add((stype, rec["id"]))
        for right in _rights_for(conn, stype, rec["id"]):
            scope, _why = _scope_of(right, now)
            if scope:
                rights.append({"subject_type": stype, "subject_id": rec["id"], **scope})
    project = gate["project"]
    package = {
        "schema_version": 1,
        "canonicalization": "FM-CJSON-1",
        "generated_at": now,
        "synthetic": True,
        "company": {"id": company_id, "name": gate["name"], "purpose": purpose},
        "organization": {"roles": [{"role": "운영 책임자", "holder": "지정 운영책임자(가상)"}]},
        "project": {"id": project["id"], "version": project["version"], "title": project["title"]},
        "catalog": {"id": gate["catalog"]["id"], "version": gate["catalog"]["version"],
                    "title": gate["catalog"]["title"]},
        "documents": documents,
        "tasks": tasks,
        "answers": answers,
        "criteria": CRITERIA,
        "samples": {"training": training if purpose == "training" else [],
                    "evaluation": evaluation if purpose == "evaluation" else []},
        "provenance": provenance,
        "rights": rights,
        "environment_definition": {"kind": "synthetic-rule", "note": "합성 규칙 환경(외부 실행 없음)"},
    }
    if gate.get("ai_draft"):
        package["ai_drafts"] = [gate["ai_draft"]]
    text = db.canonical_json(package)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return package, text, sha


def _load_package(conn, company_id):
    row = conn.execute("SELECT * FROM company_packages WHERE company_id=? ORDER BY created_at, id",
                       (company_id,)).fetchone()
    if row is None:
        return None, None, None
    return row, json.loads(row["snapshot_text"]), row["snapshot_sha256"]


def _company_view(conn, company_id):
    record = db.get_record(conn, "companies", company_id)
    if record is None or record["type"] != "companies":
        return None
    row = conn.execute("SELECT * FROM company_packages WHERE company_id=?", (company_id,)).fetchone()
    pkg = json.loads(row["snapshot_text"]) if row else None
    return record, pkg, row


def _run_rows(conn, company_id):
    runs = [dict(r) for r in conn.execute(
        "SELECT * FROM company_runs WHERE company_id=? ORDER BY task_id", (company_id,)).fetchall()]
    for r in runs:
        r["inspection_complete"] = bool(r["inspection_complete"])
    history = [dict(h) for h in conn.execute(
        "SELECT * FROM company_run_history WHERE company_id=? ORDER BY id", (company_id,)).fetchall()]
    return runs, history


@bp.get("/candidates")
def candidates():
    with closing(_conn()) as conn:
        works = []
        for r in conn.execute("SELECT id FROM records WHERE type='works' ORDER BY id").fetchall():
            w = db.get_record(conn, "works", r["id"])
            works.append({"id": w["id"], "title": w["title"], "version": w["version"],
                          "verified": _work_verified(w), "synthetic": w["synthetic"]})
        catalogs = [{"id": r["id"], "title": r["title"]} for r in conn.execute(
            "SELECT id, title FROM records WHERE type='catalog' ORDER BY id").fetchall()]
        projects = [{"id": r["id"], "title": r["title"]} for r in conn.execute(
            "SELECT id, title FROM records WHERE type='projects' ORDER BY id").fetchall()]
    return jsonify(works=works, catalogs=catalogs, projects=projects, purposes=list(PURPOSES))


@bp.get("/companies")
def list_companies():
    with closing(_conn()) as conn:
        items = []
        for r in conn.execute("SELECT id FROM records WHERE type='companies' ORDER BY created_at DESC, id").fetchall():
            rec = db.get_record(conn, "companies", r["id"])
            items.append({"id": rec["id"], "title": rec["title"], "status": rec["status"],
                          "synthetic": rec["synthetic"], "purpose": rec.get("purpose"),
                          "version": rec["version"]})
    return jsonify(items=items, total=len(items))


@bp.get("/companies/<cid>")
def company_detail(cid):
    with closing(_conn()) as conn:
        view = _company_view(conn, cid)
        if view is None:
            return _error(404, "not_found", "회사를 찾을 수 없습니다.")
        record, pkg, row = view
        now = _now()
        current = _export_gate(conn, pkg, now) if pkg else None
        runs, history = _run_rows(conn, cid)
        links, _missing = db.relations(conn, "companies", cid)
    return jsonify(item=record, package={"snapshot": pkg, "sha256": row["snapshot_sha256"],
                                         "created_at": row["created_at"]} if pkg else None,
                   current_status=current, runs=runs, history=history, links=links)


@bp.get("/companies/<cid>/samples")
def company_samples(cid):
    with closing(_conn()) as conn:
        view = _company_view(conn, cid)
        if view is None or view[1] is None:
            return _error(404, "not_found", "회사 패키지를 찾을 수 없습니다.")
        pkg = view[1]
    return jsonify(purpose=pkg["company"]["purpose"], samples=pkg["samples"],
                   sha256=view[2]["snapshot_sha256"])


@bp.post("/check")
def check_gate():
    body = request.get_json(silent=True) or {}
    with closing(_conn()) as conn:
        gate = _creation_gate(conn, body, _now())
    allowed = not gate["errors"] and not gate["holds"]
    return jsonify(allowed=allowed, errors=gate["errors"], holds=gate["holds"], checked_at=_now())


def _create(body, draft=None, run_id=None):
    """Create a company through the creation gate. `draft` is a human-reviewed AI draft for the snapshot."""
    with closing(_conn()) as conn:
        now = _now()
        with db.write_transaction(conn):
            gate = _creation_gate(conn, body, now)
            if gate["errors"]:
                return jsonify(error={"code": "prerequisite", "message": "관계·검증 조건을 먼저 보완하세요."},
                               errors=gate["errors"]), 422
            if gate["holds"]:
                return jsonify(error={"code": "hold", "message": "권리·검토 조건이 부족해 회사를 만들지 않았습니다."},
                               holds=gate["holds"]), 409
            if draft is not None:
                run = aidraft.get_run(conn, run_id, "F10.6")
                if run is None or not run["ok"]:
                    return _error(404, "not_found", "채택할 수 있는 AI 초안을 찾을 수 없습니다.")
                if run["adopted_at"]:
                    return _error(409, "already_adopted", "이미 채택한 AI 초안입니다.")
                gate["ai_draft"] = {**draft, "run_id": run_id, "model": run["model"],
                                    "label": aidraft.label(run["model"]), "origin": "ai_draft",
                                    "human_reviewed": True, "model_training_performed": False,
                                    "note": AI_NOTE}
                aidraft.mark_adopted(conn, run_id)
            company_id = db.create_record(
                conn, "companies", title=gate["name"], status="active", synthetic=True,
                fields={"purpose": gate["purpose"], "work_ids": [w["id"] for w in gate["works"]],
                        "catalog_id": gate["catalog_id"], "project_id": gate["project_id"]})
            for work in gate["works"]:
                db.add_link(conn, "companies", company_id, "works", work["id"], "work")
            db.add_link(conn, "companies", company_id, "projects", gate["project_id"], "project")
            db.add_link(conn, "companies", company_id, "catalog", gate["catalog_id"], "catalog")
            pkg, text, sha = _build_package(conn, company_id, gate, now)
            package_id = f"pkg_{sha[:16]}_{company_id}"
            conn.execute(
                "INSERT INTO company_packages (id, company_id, purpose, snapshot_text, snapshot_sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (package_id, company_id, gate["purpose"], text, sha, now))
            for task in pkg["tasks"]:
                conn.execute(
                    "INSERT INTO company_runs (company_id, task_id, attempt, state, inspection_complete, score, "
                    "run_version) VALUES (?, ?, 1, 'pending_review', 0, NULL, 1)",
                    (company_id, task["task_id"]))
            record = db.get_record(conn, "companies", company_id)
            record_fields = {"package_id": package_id, "package_sha256": sha,
                             "task_ids": [t["task_id"] for t in pkg["tasks"]]}
            db.update_record(conn, "companies", company_id, expected_version=record["version"],
                             changes={"fields": {**{k: record[k] for k in (
                                 "purpose", "work_ids", "catalog_id", "project_id")}, **record_fields}})
    return jsonify(id=company_id, package_sha256=sha, package_id=package_id), 201


@bp.post("/companies")
def create_company():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error(400, "bad_request", "JSON 객체를 보내세요.")
    return _create(body)


AI_SYSTEM = (
    "당신은 숙련 작업 기록을 회사 문서와 학습 사례로 정리하는 보조자입니다. 주어진 검증된 작업만 근거로 "
    "JSON 객체 하나만 출력합니다. 키: document_title, document_body, learning_case. "
    "근거에 없는 사실을 만들지 않고 모델을 실제로 훈련했다고 쓰지 않습니다.")
AI_NOTE = ("AI 초안을 사람이 검토·수정해 채택한 문서입니다. 권리·사람 검증 게이트를 통과한 작업만 근거이며, "
           "실제 모델 훈련을 수행했다는 뜻이 아닙니다.")
DRAFT_LIMITS = {"document_title": 120, "document_body": 4000, "learning_case": 2000}


@bp.get("/ai-status")
def ai_status():
    return jsonify(aidraft.status("F10.6"))


@bp.post("/ai-draft")
def ai_draft():
    """F10.6: only after the creation gate passes (so rights-blocked material is never sent to a model)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error(400, "bad_request", "JSON 객체를 보내세요.")
    with closing(_conn()) as conn:
        with db.write_transaction(conn):
            gate = _creation_gate(conn, body, _now())
            if gate["errors"]:
                return jsonify(error={"code": "prerequisite", "message": "관계·검증 조건을 먼저 보완하세요."},
                               errors=gate["errors"]), 422
            if gate["holds"]:
                return jsonify(error={"code": "hold", "message": "권리·검토 조건이 부족해 AI 초안을 만들지 않았습니다."},
                               holds=gate["holds"]), 409
            prompt = "\n\n".join(f"[작업 {w['id']}] {w['title']}\n{(w.get('body') or '')[:1500]}"
                                  for w in gate["works"])
            parsed, info = aidraft.draft(conn, "F10.6", AI_SYSTEM, prompt, "companies", None,
                                         detail={"work_ids": [w["id"] for w in gate["works"]]})
    draft = None
    if parsed is not None:
        draft = {k: aidraft.clip(parsed.get(k), lim) for k, lim in DRAFT_LIMITS.items()}
        draft = draft if any(draft.values()) else None
    if draft is None and info["ok"]:
        info.update(ok=False, error="empty_draft", label=aidraft.label(None), mode="manual")
    return jsonify(draft=draft, ai=info, adoptable=draft is not None, note=AI_NOTE)


@bp.post("/ai-draft/<run_id>/adopt")
def ai_draft_adopt(run_id):
    """Adopt an edited draft: the whole creation gate runs again at this moment, then it joins the snapshot."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error(400, "bad_request", "JSON 객체를 보내세요.")
    raw = body.get("draft")
    if not isinstance(raw, dict) or set(raw) - set(DRAFT_LIMITS):
        return _error(422, "validation_error", "초안(draft) 객체가 필요합니다.")
    draft = {}
    for key, limit in DRAFT_LIMITS.items():
        value = raw.get(key, "")
        if not isinstance(value, str) or len(value.strip()) > limit:
            return _error(422, "validation_error", f"{key}는 {limit}자 이하 문자열이어야 합니다.")
        draft[key] = value.strip()
    if not any(draft.values()):
        return _error(422, "validation_error", "초안 내용이 비어 있습니다.")
    return _create({k: v for k, v in body.items() if k != "draft"}, draft, run_id)


@bp.get("/companies/<cid>/export")
def export_company(cid):
    with closing(_conn()) as conn:
        view = _company_view(conn, cid)
        if view is None or view[1] is None:
            return _error(404, "not_found", "회사 패키지를 찾을 수 없습니다.")
        record, pkg, row = view
        gate = _export_gate(conn, pkg, _now())
    if not gate["allowed"]:
        resp = jsonify(error={"code": "hold", "message": "현재 권리·검토 조건으로 추가 내보내기가 보류되었습니다."},
                       holds=gate["holds"], checked_at=gate["checked_at"], snapshot_sha256=row["snapshot_sha256"])
        resp.headers["Cache-Control"] = "no-store"
        return resp, 409
    payload = {"package": pkg, "snapshot_sha256": row["snapshot_sha256"],
               "canonicalization": "FM-CJSON-1",
               "current_notice": {"authorized": True, "checked_at": gate["checked_at"],
                                  "external_delivery": "not_authorized",
                                  "message": "로컬 다운로드입니다. 외부 전달 허용으로 표시되지 않습니다."}}
    resp = Response(json.dumps(payload, ensure_ascii=False, indent=2),
                    mimetype="application/json")
    resp.headers["Content-Disposition"] = f'attachment; filename="company-{cid}.json"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.get("/companies/<cid>/runs")
def list_runs(cid):
    with closing(_conn()) as conn:
        runs, history = _run_rows(conn, cid)
    return jsonify(runs=runs, history=history)


@bp.post("/companies/<cid>/tasks/<tid>/action")
def task_action(cid, tid):
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    if action not in RUN_ACTIONS:
        return _error(400, "bad_request", "action은 request_inspection/hold/release 중 하나입니다.")
    with closing(_conn()) as conn:
        with db.write_transaction(conn):
            run = conn.execute("SELECT * FROM company_runs WHERE company_id=? AND task_id=?",
                               (cid, tid)).fetchone()
            if run is None:
                return _error(404, "not_found", "과제를 찾을 수 없습니다.")
            if run["run_version"] != body.get("expected_run_version"):
                return _error(409, "version_conflict", "실행 버전이 바뀌었습니다. 새로고침 후 다시 시도하세요.",
                              run_version=run["run_version"])
            state = run["state"]
            now = _now()
            if state == "released":
                return _error(409, "released", "이미 해제된 과제입니다. 초기화한 뒤 새 시도로 진행하세요.")
            if action == "release" and not run["inspection_complete"]:
                conn.execute(
                    "INSERT INTO company_run_history (company_id, task_id, attempt, event, from_state, to_state, "
                    "score, detail_json, created_at) VALUES (?, ?, ?, 'release_blocked', ?, ?, ?, ?, ?)",
                    (cid, tid, run["attempt"], state, state, run["score"],
                     '{"reason":"검사 전 해제 거절"}', now))
                return _error(409, "inspection_required", "검사 전 해제는 거절됩니다. 먼저 검사를 요청하세요.")
            score = run["score"]
            if action == "request_inspection":
                view = _company_view(conn, cid)
                if view is None or view[1] is None:
                    return _error(404, "not_found", "회사 패키지를 찾을 수 없습니다.")
                pkg = view[1]
                task = next((t for t in pkg["tasks"] if t["task_id"] == tid), None)
                if task is None:
                    return _error(404, "not_found", "패키지에 없는 과제입니다.")
                gate = _export_gate(conn, pkg, now)
                answer = next((a for a in pkg["answers"] if a["task_id"] == tid), {})
                checks = {"answer_present": bool(answer.get("answer")),
                          "provenance_linked": any(p["id"] == task["work_id"] for p in pkg["provenance"]),
                          "rights_ok": gate["allowed"],
                          "reviewer_ok": all(_expert_approved(e) for e in
                                             [db.get_record(conn, "experts", p["id"]) or {}
                                              for p in pkg["provenance"] if p["type"] == "experts"])}
                score = sum(1 for v in checks.values() if v)
                new_state, inspected = "inspected", 1
                detail = {"checks": checks, "rule": "통과 항목 수 0~4"}
            elif action == "hold":
                new_state, inspected, detail = "held", run["inspection_complete"], {}
            else:
                new_state, inspected, detail = "released", 1, {}
            conn.execute("UPDATE company_runs SET state=?, inspection_complete=?, score=?, run_version=? "
                         "WHERE company_id=? AND task_id=?",
                         (new_state, inspected, score, run["run_version"] + 1, cid, tid))
            conn.execute(
                "INSERT INTO company_run_history (company_id, task_id, attempt, event, from_state, to_state, "
                "score, detail_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, tid, run["attempt"], action, state, new_state, score,
                 json.dumps(detail, ensure_ascii=False, sort_keys=True), now))
            updated = conn.execute("SELECT * FROM company_runs WHERE company_id=? AND task_id=?",
                                   (cid, tid)).fetchone()
    return jsonify(run={**dict(updated), "inspection_complete": bool(updated["inspection_complete"])})


@bp.post("/companies/<cid>/tasks/<tid>/reset")
def task_reset(cid, tid):
    body = request.get_json(silent=True) or {}
    with closing(_conn()) as conn:
        with db.write_transaction(conn):
            run = conn.execute("SELECT * FROM company_runs WHERE company_id=? AND task_id=?",
                               (cid, tid)).fetchone()
            if run is None:
                return _error(404, "not_found", "과제를 찾을 수 없습니다.")
            if run["run_version"] != body.get("expected_run_version"):
                return _error(409, "version_conflict", "실행 버전이 바뀌었습니다. 새로고침 후 다시 시도하세요.",
                              run_version=run["run_version"])
            attempt = run["attempt"] + 1
            conn.execute("UPDATE company_runs SET attempt=?, state='pending_review', inspection_complete=0, "
                         "score=NULL, run_version=? WHERE company_id=? AND task_id=?",
                         (attempt, run["run_version"] + 1, cid, tid))
            conn.execute(
                "INSERT INTO company_run_history (company_id, task_id, attempt, event, from_state, to_state, "
                "score, detail_json, created_at) VALUES (?, ?, ?, 'reset', ?, 'pending_review', NULL, '{}', ?)",
                (cid, tid, attempt, run["state"], _now()))
            updated = conn.execute("SELECT * FROM company_runs WHERE company_id=? AND task_id=?",
                                   (cid, tid)).fetchone()
    return jsonify(run={**dict(updated), "inspection_complete": bool(updated["inspection_complete"])})


@bp.post("/rights")
def supplement_rights():
    """Record a new rights scope for a subject as a new rights record (never edits old ones)."""
    body = request.get_json(silent=True) or {}
    stype, sid = body.get("subject_type"), body.get("subject_id")
    purposes = body.get("purposes") or []
    evidence = str(body.get("evidence") or "").strip()
    confirmed_by = str(body.get("confirmed_by") or "").strip()
    if stype not in ("materials", "experts", "works"):
        return _error(400, "bad_request", "대상 유형은 materials/experts/works 중 하나입니다.")
    if not isinstance(purposes, list) or any(p not in PURPOSES for p in purposes):
        return _error(400, "bad_request", "목적은 training/evaluation 목록입니다.")
    if not evidence or not confirmed_by:
        return _error(400, "bad_request", "확인 근거와 확인자를 입력하세요.")
    with closing(_conn()) as conn:
        with db.write_transaction(conn):
            subject = db.get_record(conn, stype, sid) if isinstance(sid, str) else None
            if subject is None:
                return _error(404, "not_found", "권리 대상을 찾을 수 없습니다.")
            now = _now()
            status = "confirmed" if purposes else "unconfirmed"
            rid = db.create_record(
                conn, "rights", title=f"권리 보완: {subject['title']}", status=status, synthetic=True,
                fields={"purposes": sorted(purposes), "target_type": stype, "target_id": sid,
                        "subject_type": stype, "subject_id": sid,
                        "subject_version": subject["version"],
                        "subject_material_hash": subject["material_hash"],
                        "derivative": body.get("derivative") or "unknown",
                        "distribution": body.get("distribution") or "unknown",
                        "evidence": evidence, "confirmed_by": confirmed_by, "confirmed_at": now})
            db.add_link(conn, "rights", rid, stype, sid, "subject")
            rec = db.get_record(conn, "rights", rid)
    return jsonify(item=rec), 201


@bp.post("/approvals/revoke")
def revoke_approval():
    """Record a specific past approval revocation. Does not restore or revoke anything else."""
    body = request.get_json(silent=True) or {}
    stype, sid = body.get("subject_type"), body.get("subject_id")
    version = body.get("subject_version")
    reason = str(body.get("reason") or "").strip()
    evidence = str(body.get("evidence") or "").strip()
    actor = str(body.get("actor") or "").strip()
    if stype not in ("materials", "experts", "works") or not isinstance(sid, str):
        return _error(400, "bad_request", "취소 대상 유형과 ID를 지정하세요.")
    if not isinstance(version, int) or isinstance(version, bool):
        return _error(400, "bad_request", "취소할 대상 버전(정수)을 지정하세요.")
    if not reason or not evidence or not actor:
        return _error(400, "bad_request", "사유·근거·담당자를 모두 입력하세요.")
    with closing(_conn()) as conn:
        with db.write_transaction(conn):
            if db.get_record(conn, stype, sid) is None:
                return _error(404, "not_found", "취소 대상을 찾을 수 없습니다.")
            cur = conn.execute(
                "INSERT INTO company_events (kind, subject_type, subject_id, subject_version, reason, evidence, "
                "actor, created_at) VALUES ('revoke', ?, ?, ?, ?, ?, ?, ?)",
                (stype, sid, version, reason, evidence, actor, _now()))
            event_id = cur.lastrowid
    return jsonify(event={"id": event_id, "subject_type": stype, "subject_id": sid,
                          "subject_version": version, "reason": reason, "evidence": evidence,
                          "actor": actor}), 201
