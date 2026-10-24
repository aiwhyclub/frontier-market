import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock
import json

from backend import db
from backend.app import create_app
from backend.features import refine

ROOT = Path(__file__).resolve().parent.parent.parent


class RefineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "records.sqlite3"
        self.app = create_app(ROOT, self.db_path)
        if "refine" not in self.app.blueprints:
            self.app.register_blueprint(refine.bp)
        self.client = self.app.test_client()
        self.client.environ_base["HTTP_X_FM_LANG"] = "ko"  # these tests assert the Korean message text

    def tearDown(self):
        self.tmp.cleanup()

    def conn(self):
        return db.connect(self.db_path)

    def make_work(self, expert="exp_seed01", catalog="cat_seed01", body="관찰: 치수를 두 번 확인한다. 다음 단계로 간다."):
        with closing(self.conn()) as conn:
            with db.write_transaction(conn):
                rid = db.create_record(conn, "works", title="테스트 작업", status="uploaded",
                                       synthetic=True, body=body, fields={"evidence": ["합성 근거"]})
                if expert:
                    db.add_link(conn, "works", rid, "experts", expert, "by")
                if catalog:
                    db.add_link(conn, "works", rid, "catalog", catalog, "for")
        return rid

    def detail(self, wid):
        r = self.client.get(f"/api/refine/works/{wid}")
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()

    def version(self, wid):
        return self.detail(wid)["item"]["version"]

    def set_operator(self, name="운영자 A"):
        r = self.client.post("/api/refine/operator", json={"name": name})
        self.assertEqual(r.status_code, 200)
        return name

    def correct(self, wid, text="교정된 관찰 내용", reason="원문 표현을 보완"):
        r = self.client.post(f"/api/refine/works/{wid}/correction", json={
            "expected_version": self.version(wid), "refined": {"observation": text},
            "correction_reason": reason, "reviewer": "운영자 A", "from_draft": False})
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()

    def test_prereq_flags_missing_catalog_with_fix_link(self):
        d = self.detail("wrk_seed02")
        catalog = [c for c in d["prereq"]["checks"] if c["key"] == "catalog"][0]
        self.assertFalse(catalog["ok"])
        self.assertEqual(catalog["fix_link"], "#s4")
        self.assertFalse(d["prereq"]["ready"])

    def test_prereq_flags_unapproved_expert(self):
        wid = self.make_work(expert="exp_seed02")
        check = [c for c in self.detail(wid)["prereq"]["checks"] if c["key"] == "expert"][0]
        self.assertFalse(check["ok"])
        self.assertEqual(check["fix_link"], "#s3")
        r = self.client.post(f"/api/refine/works/{wid}/draft", json={"expected_version": 1})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"]["code"], "prereq_unmet")

    def test_rule_draft_is_not_a_review_and_keeps_input_hash(self):
        wid = self.make_work()
        before = self.detail(wid)
        self.assertTrue(before["prereq"]["ready"])
        r = self.client.post(f"/api/refine/works/{wid}/draft", json={"expected_version": before["item"]["version"]})
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(d["views"]["draft"]["method"], "rule")
        self.assertIn("관찰 사실", d["views"]["draft"]["content"]["observation"])
        self.assertEqual(d["input_hash"], before["input_hash"])
        self.assertEqual(d["review"]["state"], "no_correction")

    def test_correction_creates_new_version_and_keeps_original(self):
        wid = self.make_work()
        d = self.correct(wid)
        self.assertEqual(d["views"]["corrected"]["method"], "human")
        self.assertEqual(d["views"]["original"]["body"], "관찰: 치수를 두 번 확인한다. 다음 단계로 간다.")
        self.assertEqual(d["review"]["state"], "pending")

    def test_validate_requires_designated_operator_and_current_hash(self):
        wid = self.make_work()
        self.correct(wid)
        d = self.detail(wid)
        body = {"decision": "approved", "notes": "확인함", "reviewer": "운영자 A",
                "expected_version": d["item"]["version"], "input_hash": d["input_hash"], "evidence_refs": []}
        r = self.client.post(f"/api/refine/works/{wid}/validate", json=body)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"]["code"], "operator_not_designated")
        self.set_operator()
        body["reviewer"] = "다른 사람"
        self.assertEqual(self.client.post(f"/api/refine/works/{wid}/validate", json=body).status_code, 403)
        body["reviewer"] = "운영자 A"
        body["input_hash"] = "0" * 64
        r = self.client.post(f"/api/refine/works/{wid}/validate", json=body)
        self.assertEqual(r.get_json()["error"]["code"], "stale_input")
        body["input_hash"] = d["input_hash"]
        r = self.client.post(f"/api/refine/works/{wid}/validate", json=body)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["review"]["state"], "approved")
        again = self.client.post(f"/api/refine/works/{wid}/validate", json=body)
        self.assertEqual(again.status_code, 409)

    def test_original_revision_makes_approval_stale_and_keeps_history(self):
        wid = self.make_work()
        self.correct(wid)
        self.set_operator()
        d = self.detail(wid)
        r = self.client.post(f"/api/refine/works/{wid}/validate", json={
            "decision": "approved", "notes": "확인함", "reviewer": "운영자 A",
            "expected_version": d["item"]["version"], "input_hash": d["input_hash"]})
        self.assertEqual(r.get_json()["review"]["state"], "approved")
        rv = self.client.post(f"/api/refine/works/{wid}/revise", json={
            "expected_version": d["item"]["version"], "body": "개정된 원문 내용입니다.",
            "reason": "현장 표현 수정", "reviewer": "운영자 A"})
        self.assertEqual(rv.status_code, 200, rv.get_json())
        after = rv.get_json()
        self.assertEqual(after["review"]["state"], "review_required")
        self.assertEqual(len(after["revisions"]), 1)
        self.assertEqual(len(after["review"]["reviews"]), 1)
        self.assertFalse(after["review"]["reviews"][0]["current_input"])
        with closing(self.conn()) as conn:
            old = conn.execute("SELECT snapshot_json FROM record_revisions WHERE type='works' AND id=? AND version=?",
                               (wid, d["item"]["version"])).fetchone()
        self.assertIn("관찰: 치수를 두 번 확인한다.", old["snapshot_json"])

    def test_list_endpoint_reports_state(self):
        r = self.client.get("/api/refine/works")
        self.assertEqual(r.status_code, 200)
        ids = {i["id"]: i for i in r.get_json()["items"]}
        self.assertIn("wrk_seed01", ids)
        self.assertIn(ids["wrk_seed01"]["state"], {"no_correction", "pending", "approved", "rejected", "review_required"})

    # F10.2 AI refine draft (ai.attempt monkeypatched; no network)

    def ai_run(self, ok=True, error=None):
        return {"attempted": True, "provider": "anthropic", "model": "claude-test", "ok": ok, "error": error,
                "started_at": "t0", "finished_at": "t1", "input_hash": "sha256:h", "usage": {"output_tokens": 3}}

    def ai_request(self, wid, text, run):
        with mock.patch("backend.ai.adopted", return_value=True), \
                mock.patch("backend.ai.attempt", return_value=(text, run)):
            return self.client.post(f"/api/refine/works/{wid}/ai-draft",
                                    json={"expected_version": self.version(wid)})

    def approved_work(self):
        wid = self.make_work()
        self.correct(wid)
        self.set_operator()
        d = self.detail(wid)
        r = self.client.post(f"/api/refine/works/{wid}/validate", json={
            "decision": "approved", "notes": "ok", "reviewer": "운영자 A",
            "expected_version": d["item"]["version"], "input_hash": d["input_hash"], "evidence_refs": []})
        self.assertEqual(r.status_code, 200, r.get_json())
        return wid

    def test_ai_draft_never_changes_approval_or_original_and_needs_adopt_then_new_review(self):
        wid = self.approved_work()
        before = self.detail(wid)
        text = json.dumps({"observation": "AI가 정리한 관찰", "context": "맥락"}, ensure_ascii=False)
        r = self.ai_request(wid, text, self.ai_run())
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(d["views"]["ai_draft"]["status"], "proposed")
        self.assertEqual(d["views"]["ai_draft"]["model"], "claude-test")
        self.assertEqual(d["views"]["original"], before["views"]["original"])
        self.assertEqual(d["views"]["corrected"], before["views"]["corrected"])
        self.assertEqual((d["review"]["state"], d["input_hash"]), ("approved", before["input_hash"]))
        # using it before adoption is refused
        r = self.client.post(f"/api/refine/works/{wid}/correction", json={
            "expected_version": d["item"]["version"], "refined": {"observation": "x"}, "correction_reason": "r",
            "reviewer": "운영자 A", "from_ai_draft": True})
        self.assertEqual(r.get_json()["error"]["code"], "ai_draft_not_adopted")
        r = self.client.post(f"/api/refine/works/{wid}/ai-draft/adopt", json={
            "expected_version": d["item"]["version"], "reviewer": "운영자 A"})
        d = r.get_json()
        self.assertEqual(d["views"]["ai_draft"]["status"], "adopted")
        self.assertEqual(d["review"]["state"], "approved")
        r = self.client.post(f"/api/refine/works/{wid}/correction", json={
            "expected_version": d["item"]["version"], "refined": {"observation": "AI가 정리한 관찰"},
            "correction_reason": "AI 초안 교정", "reviewer": "운영자 A", "from_ai_draft": True})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["review"]["state"], "review_required")

    def test_ai_failure_reject_and_off_leave_state_untouched(self):
        wid = self.approved_work()
        before = self.detail(wid)
        r = self.ai_request(wid, None, self.ai_run(ok=False, error="URLError: x"))
        d = r.get_json()
        self.assertFalse(d["ai_result"]["ok"])
        self.assertIsNone(d["views"]["ai_draft"]["content"])
        self.assertEqual(len(d["ai_attempts"]), 1)
        r = self.ai_request(wid, "garbage", self.ai_run())
        self.assertEqual(r.get_json()["ai_attempts"][-1]["error"], "unparsable_response")
        r = self.ai_request(wid, json.dumps({"observation": "AI"}), self.ai_run())
        d = r.get_json()
        r = self.client.post(f"/api/refine/works/{wid}/ai-draft/reject", json={
            "expected_version": d["item"]["version"], "reviewer": "운영자 A"})
        d = r.get_json()
        self.assertEqual(d["views"]["ai_draft"]["status"], "rejected")
        self.assertEqual(len(d["ai_attempts"]), 3)
        self.assertEqual((d["review"]["state"], d["input_hash"]), ("approved", before["input_hash"]))
        self.assertEqual(d["views"]["corrected"], before["views"]["corrected"])
        r = self.client.post(f"/api/refine/works/{wid}/ai-draft", json={"expected_version": d["item"]["version"]})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"]["code"], "ai_not_adopted")
        self.assertFalse(d["ai"]["adopted"])


if __name__ == "__main__":
    unittest.main()
