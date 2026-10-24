"""AC-13 end-to-end (STORY 11.5): rights confirmation/approval loops, content change vs revoke,
task run isolation, and HOLD after rights expiry/revoke/unconfirmed. Uses a temp DB only."""

import json
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from backend import db
from backend.app import create_app

ROOT = Path(__file__).resolve().parent.parent


class AC13Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "ac13.sqlite3"
        self.app = create_app(ROOT, self.db_path)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    # helpers -----------------------------------------------------------------
    def _conn(self):
        return db.connect(self.db_path)

    def _supplement(self, stype, sid, purposes=("training",)):
        r = self.client.post("/api/company/rights", json={
            "subject_type": stype, "subject_id": sid, "purposes": list(purposes),
            "evidence": "합성 확인 근거", "confirmed_by": "가상 운영자"})
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["item"]["id"]

    def _supplement_core(self, works=("wrk_seed01",)):
        for w in works:
            self._supplement("works", w)
        self._supplement("experts", "exp_seed01")
        self._supplement("materials", "mat_seed03")

    def _body(self, work_ids=("wrk_seed01",), purpose="training"):
        return {"name": "AC13 가상 회사", "work_ids": list(work_ids), "catalog_id": "cat_seed01",
                "project_id": "prj_seed01", "purpose": purpose}

    def _create(self, work_ids=("wrk_seed01",)):
        r = self.client.post("/api/company/companies", json=self._body(work_ids))
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["id"]

    def _export(self, cid):
        return self.client.get(f"/api/company/companies/{cid}/export")

    def _check(self, **over):
        return self.client.post("/api/company/check", json=self._body(**over)).get_json()

    def _db_update(self, rtype, rid, **changes):
        with closing(self._conn()) as conn:
            with db.write_transaction(conn):
                cur = db.get_record(conn, rtype, rid)
                return db.update_record(conn, rtype, rid, expected_version=cur["version"], changes=changes)

    def _rights_grant(self, **over):
        payload = {
            "subject_type": "works", "subject_id": "wrk_seed01", "subject_version": 1,
            "holder": "가상 권리자", "purposes": ["training"], "derivative": "allowed",
            "distribution": "denied", "reason": "합성 학습 허용 확인", "evidence": "합성 동의 문서",
            "actor": "운영책임자A", "status": "confirmed",
        }
        payload.update(over)
        return self.client.post("/api/rights", json=payload)

    def _rights_patch(self, rid, expected, **over):
        body = {"expected_version": expected, "reason": "상태 변경 검토", "actor": "운영책임자A",
                "status": "confirmed", "evidence": "합성 동의 문서", "purposes": ["training"],
                "derivative": "allowed", "distribution": "denied", "holder": "가상 권리자"}
        body.update(over)
        return self.client.patch(f"/api/rights/{rid}", json=body)

    # AC-13 scenarios ---------------------------------------------------------
    def test_status_only_bump_and_identical_reconfirmation_do_not_loop(self):
        self._supplement_core()
        cid = self._create()
        self._db_update("works", "wrk_seed01", status="reviewed")  # status-only, same content
        self.assertTrue(self._check()["allowed"])
        self.assertEqual(self._export(cid).status_code, 200)
        state = self.client.get("/api/rights/state?subject_type=works&subject_id=wrk_seed01"
                                "&purpose=training").get_json()
        self.assertEqual(state["state"], "allowed")
        self.assertEqual(state["subject"]["version"], 2)

        # identical rights confirmation: no new version and no new event row
        rid = self._rights_grant().get_json()["item"]["id"]
        before = self.client.get(f"/api/rights/{rid}").get_json()["events"]
        r = self._rights_patch(rid, 1, reason="합성 학습 허용 확인")  # same content as the grant
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["item"]["version"], 1)
        after = self.client.get(f"/api/rights/{rid}").get_json()["events"]
        self.assertEqual(len(before), len(after))

    def test_content_change_needs_reconfirmation_and_past_package_stays_frozen(self):
        self._supplement_core()
        cid = self._create()
        self._db_update("works", "wrk_seed01", body="내용이 바뀐 합성 작업 원문입니다.")
        check = self._check()
        self.assertFalse(check["allowed"])
        self.assertTrue(any(h["target_id"] == "wrk_seed01" for h in check["holds"]))
        # the frozen package still pins v1 and its grant is bound to v1 content: export stays allowed
        self.assertEqual(self._export(cid).status_code, 200)
        self._supplement("works", "wrk_seed01")
        self.assertTrue(self._check()["allowed"])

    def test_revoking_past_approval_is_distinct_from_content_change(self):
        self._supplement_core()
        cid = self._create()
        self._db_update("works", "wrk_seed01", body="새 내용 v2 본문입니다.")
        self._supplement("works", "wrk_seed01")
        r = self.client.post("/api/company/approvals/revoke", json={
            "subject_type": "works", "subject_id": "wrk_seed01", "subject_version": 1,
            "reason": "검토 오류", "evidence": "검토 기록", "actor": "지정 운영책임자"})
        self.assertEqual(r.status_code, 201)
        ex = self._export(cid)
        self.assertEqual(ex.status_code, 409)
        self.assertTrue(any(h["action"] == "approval_revoked" for h in ex.get_json()["holds"]))
        # the revocation is for v1 only: the current v2 creation is not held and nothing is restored
        self.assertTrue(self._check()["allowed"])

    def test_task_run_and_reset_do_not_change_other_task_or_frozen_hash(self):
        with closing(self._conn()) as conn:
            with db.write_transaction(conn):
                db.add_link(conn, "works", "wrk_seed02", "catalog", "cat_seed01", "for")
        self._supplement_core(works=("wrk_seed01", "wrk_seed02"))
        cid = self._create(work_ids=("wrk_seed01", "wrk_seed02"))
        base = f"/api/company/companies/{cid}"
        before = self.client.get(base).get_json()
        sha0, snap0 = before["package"]["sha256"], before["package"]["snapshot"]
        b0 = next(r for r in before["runs"] if r["task_id"] == "tsk_wrk_seed02")

        a = "tsk_wrk_seed01"
        r = self.client.post(f"{base}/tasks/{a}/action", json={"action": "request_inspection", "expected_run_version": 1})
        self.assertEqual(r.status_code, 200, r.get_json())
        r = self.client.post(f"{base}/tasks/{a}/action", json={"action": "release", "expected_run_version": 2})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(f"{base}/tasks/{a}/reset", json={"expected_run_version": 3})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["run"]["attempt"], 2)

        after = self.client.get(base).get_json()
        self.assertEqual(after["package"]["sha256"], sha0)
        self.assertEqual(after["package"]["snapshot"], snap0)
        b1 = next(r for r in after["runs"] if r["task_id"] == "tsk_wrk_seed02")
        self.assertEqual(b1, b0)
        self.assertTrue(all(h["task_id"] == a for h in after["history"]
                            if h["event"] in ("request_inspection", "release", "reset")))

    def test_expiry_revoke_unconfirmed_hold_further_export_and_creation(self):
        self._supplement("experts", "exp_seed01")
        self._supplement("materials", "mat_seed03")
        self._supplement("materials", "mat_seed01")
        r = self._rights_grant(valid_until=(date.today() + timedelta(days=30)).isoformat())
        self.assertEqual(r.status_code, 201, r.get_json())
        rid = r.get_json()["item"]["id"]
        cid = self._create()
        self.assertEqual(self._export(cid).status_code, 200)

        r = self._rights_patch(rid, 1, status="unconfirmed", evidence=None)
        self.assertEqual(r.status_code, 200, r.get_json())
        ex = self._export(cid)
        self.assertEqual(ex.status_code, 409)
        self.assertTrue(any(h["target_id"] == "wrk_seed01" for h in ex.get_json()["holds"]))
        self.assertFalse(self._check()["allowed"])

        r = self._rights_patch(rid, 2, status="confirmed")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self._export(cid).status_code, 200)

        r = self._rights_patch(rid, 3, status="revoked")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self._export(cid).status_code, 409)

        r = self._rights_patch(rid, 4, status="confirmed")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self._export(cid).status_code, 200)
        with closing(self._conn()) as conn:
            with db.write_transaction(conn):
                row = db.get_record(conn, "rights", rid)
                fields = {k: row[k] for k in ("subject_type", "subject_id", "subject_version",
                                              "subject_material_hash", "holder", "purposes",
                                              "derivative", "distribution", "reason", "evidence",
                                              "confirmed_by", "confirmed_at")}
                fields["valid_until"] = (date.today() - timedelta(days=1)).isoformat()
                db.update_record(conn, "rights", rid, expected_version=row["version"],
                                 changes={"fields": fields})
        ex = self._export(cid)
        self.assertEqual(ex.status_code, 409)
        self.assertTrue(any("기간 만료" in h["reason"] for h in ex.get_json()["holds"]))
        self.assertEqual(self.client.get(f"/api/rights/state?subject_type=works&subject_id=wrk_seed01"
                                         "&purpose=training").get_json()["state"], "hold")


if __name__ == "__main__":
    unittest.main()
