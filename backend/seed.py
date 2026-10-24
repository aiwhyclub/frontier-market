"""Initial synthetic seed (C-04 A): synthetic records only, applied once per database.

Every record is marked synthetic. Seed ids are fixed, and a metadata marker records the seed
version, so restarting never duplicates the seed. Existing rows are never overwritten, and
user data is never reset.
"""

from contextlib import closing

from backend import db

SEED_VERSION = 1
SEED_MARKER_KEY = "seed_version"

# (type, id, title, status, body, fields)
SEED_RECORDS = [
    ("materials", "mat_seed01", "가상 관찰 노트: 목공 도제의 치수 확인", "draft",
     "합성 가공 사례입니다. 실제 인물·현장과 무관하며 치수를 두 번 확인하는 순서를 가상으로 적었습니다.",
     {"kind": "observation", "provenance": "합성 가공 사례"}),
    ("materials", "mat_seed02", "가상 연습자료: 공예 판단 체크포인트", "draft",
     "합성 가공 사례입니다. 공개 연습자료가 아니며 평가 전용 권리 예시로만 쓰입니다.",
     {"kind": "practice", "provenance": "합성 가공 사례"}),
    ("materials", "mat_seed03", "가상 현장 메모: 접합부 판단 차이", "draft",
     "합성 가공 사례입니다. 권리 확인 기록이 없는 미확인 자료 예시입니다.",
     {"kind": "field_note", "provenance": "합성 가공 사례"}),
    ("experts", "exp_seed01", "가상 숙련자 A", "approved", None,
     {"field": "목공(가상)", "experience": "가상 선임과 반복 실습", "evaluation_status": "approved"}),
    ("experts", "exp_seed02", "가상 후보 B", "candidate", None,
     {"field": "공예(가상)", "experience": "가상 관찰 기록", "evaluation_status": "not_approved"}),
    ("works", "wrk_seed01", "가상 작업: 치수 확인 순서 정리", "uploaded", "합성 작업 원문입니다.",
     {"provenance": "합성 가공 사례"}),
    ("works", "wrk_seed02", "가상 작업: 접합부 판단 메모", "uploaded_unlinked", "합성 작업 원문입니다.",
     {"provenance": "합성 가공 사례"}),
    ("catalog", "cat_seed01", "가상 판단 과제: 목재 치수 확인", "draft", None,
     {"domain": "목공(가상)", "target_capability": "치수 확인 판단"}),
    ("projects", "prj_seed01", "가상 현장 프로젝트", "scoping", None,
     {"owner": "가상 운영자", "company_name": "가상 기업"}),
    ("companies", "co_seed01", "가상 정밀기업", "draft", None,
     {"purpose": "evaluation"}),
    ("rights", "rgt_seed01", "권리: 치수 노트 학습 허용", "learning_allowed", None,
     {"purposes": ["learning", "evaluation"]}),
    ("rights", "rgt_seed02", "권리: 연습자료 평가 전용", "eval_only", None,
     {"purposes": ["evaluation"]}),
    ("rights", "rgt_seed03", "권리: 후보 B 제공 범위 미확인", "unconfirmed", None,
     {"purposes": []}),
    ("feedback", "fb_seed01", "공예 도제 판단 과제 제안", None, "합성 의견 예시입니다.",
     {"kind": "topic"}),
]

# (from_type, from_id, to_type, to_id, relation)
SEED_LINKS = [
    ("works", "wrk_seed01", "experts", "exp_seed01", "by"),
    ("works", "wrk_seed01", "materials", "mat_seed01", "from"),
    ("works", "wrk_seed01", "catalog", "cat_seed01", "for"),
    ("works", "wrk_seed02", "experts", "exp_seed01", "by"),
    ("works", "wrk_seed02", "materials", "mat_seed03", "from"),
    ("catalog", "cat_seed01", "materials", "mat_seed01", "from"),
    ("projects", "prj_seed01", "catalog", "cat_seed01", "catalog"),
    ("companies", "co_seed01", "works", "wrk_seed01", "work"),
    ("companies", "co_seed01", "projects", "prj_seed01", "project"),
    ("rights", "rgt_seed01", "materials", "mat_seed01", "subject"),
    ("rights", "rgt_seed02", "materials", "mat_seed02", "subject"),
    ("rights", "rgt_seed03", "experts", "exp_seed02", "subject"),
]


def apply_seed(conn):
    """Insert missing seed rows once. Caller must hold a write transaction."""
    if db.get_metadata(conn, SEED_MARKER_KEY) == str(SEED_VERSION):
        return False
    for rtype, rid, title, status, body, fields in SEED_RECORDS:
        if db.get_record(conn, rtype, rid) is None:
            db.create_record(conn, rtype, record_id=rid, title=title, status=status,
                             synthetic=True, body=body, fields=fields)
    for from_type, from_id, to_type, to_id, relation in SEED_LINKS:
        db.add_link(conn, from_type, from_id, to_type, to_id, relation)
    db.set_metadata(conn, SEED_MARKER_KEY, str(SEED_VERSION))
    return True


def ensure_seed(db_path):
    """Apply the synthetic seed if this database has not received it yet. Returns True if applied."""
    with closing(db.connect(db_path)) as conn:
        with db.write_transaction(conn):
            return apply_seed(conn)
