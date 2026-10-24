import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock

from backend import db
from backend.app import create_app
from backend.features import experts

ROOT = Path(__file__).resolve().parent.parent.parent
GOOD_SCORES = {"apprenticeship": 3, "signals": 2, "judgment": 1, "evidence": 0}


class ExpertsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "e.sqlite3"
        self.app = create_app(ROOT, self.db_path)
        if "experts" not in self.app.blueprints:
            self.app.register_blueprint(experts.bp)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def _create(self, **over):
        body = {"name": "가상 전문가", "field": "목공(가상)", "experience": "가상 경험 5년",
                "apprenticeship": "관찰 기록과 실습 피드백", "synthetic": True}
        body.update(over)
        r = self.client.post("/api/experts", json=body)
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["item"]

    def _version(self, eid):
        return self.client.get(f"/api/experts/{eid}").get_json()["item"]["version"]

    def _evaluate(self, eid, decision, scores=GOOD_SCORES, **over):
        body = {"expected_version": self._version(eid), "decision": decision, "scores": scores,
                "notes": "근거 확인 완료", "reviewer": "지정 운영책임자"}
        body.update(over)
        return self.client.post(f"/api/experts/{eid}/evaluate", json=body)

    def test_create_candidate_is_listed_with_synthetic_flag(self):
        item = self._create()
        self.assertEqual(item["status"], "candidate")
        self.assertTrue(item["synthetic"])
        r = self.client.get("/api/records/experts")
        self.assertIn(item["id"], [i["id"] for i in r.get_json()["items"]])

    def test_create_requires_fields(self):
        r = self.client.post("/api/experts", json={"name": "", "field": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("experience", r.get_json()["error"]["fields"])

    def test_evaluation_sums_server_side_and_keeps_history(self):
        eid = self._create()["id"]
        r = self._evaluate(eid, "supplement")
        self.assertEqual(r.status_code, 201, r.get_json())
        body = r.get_json()
        self.assertEqual(body["current_verdict"]["evaluation"]["score"], 6)
        self.assertEqual(body["current_verdict"]["evaluation"]["weights"]["signals"], 0.25)
        self.assertEqual(body["item"]["status"], "supplement")
        self._evaluate(eid, "approved", scores={a: 3 for a in experts.AXES})
        detail = self.client.get(f"/api/experts/{eid}").get_json()
        self.assertEqual(len(detail["past_evaluations"]), 2)
        self.assertEqual(detail["past_evaluations"][1]["decision"], "supplement")
        self.assertTrue(detail["upload_allowed"])

    def test_high_score_alone_does_not_approve(self):
        eid = self._create()["id"]
        r = self.client.post(f"/api/experts/{eid}/evaluate", json={
            "expected_version": 1, "scores": {a: 3 for a in experts.AXES}, "notes": "n", "reviewer": "r"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("decision", r.get_json()["error"]["fields"])
        self.assertEqual(self.client.get(f"/api/experts/{eid}").get_json()["item"]["status"], "candidate")

    def test_null_scores_rejected_and_zero_is_distinct(self):
        eid = self._create()["id"]
        r = self._evaluate(eid, "rejected", scores={**GOOD_SCORES, "judgment": None})
        self.assertEqual(r.status_code, 400)
        self.assertIn("scores.judgment", r.get_json()["error"]["fields"])
        r = self._evaluate(eid, "rejected", scores={a: 0 for a in experts.AXES})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["current_verdict"]["evaluation"]["score"], 0)

    def test_out_of_range_bool_and_client_total_rejected(self):
        eid = self._create()["id"]
        for bad in ({**GOOD_SCORES, "signals": 4}, {**GOOD_SCORES, "signals": True},
                    {**GOOD_SCORES, "signals": 1.5}, {**GOOD_SCORES, "signals": "2"}):
            self.assertEqual(self._evaluate(eid, "approved", scores=bad).status_code, 400, bad)
        r = self._evaluate(eid, "approved", score=12)
        self.assertEqual(r.status_code, 400)

    def test_evaluation_requires_reviewer_and_notes(self):
        eid = self._create()["id"]
        self.assertEqual(self._evaluate(eid, "approved", reviewer="").status_code, 400)
        self.assertEqual(self._evaluate(eid, "approved", notes="  ").status_code, 400)

    def test_stale_version_conflicts(self):
        eid = self._create()["id"]
        self._evaluate(eid, "supplement")
        r = self.client.post(f"/api/experts/{eid}/evaluate", json={
            "expected_version": 1, "decision": "approved", "scores": GOOD_SCORES,
            "notes": "n", "reviewer": "r"})
        self.assertEqual(r.status_code, 409)

    def test_profile_edit_keeps_history_and_flags_reevaluation(self):
        eid = self._create()["id"]
        self._evaluate(eid, "approved")
        r = self.client.patch(f"/api/experts/{eid}", json={
            "expected_version": self._version(eid), "experience": "가상 경험 10년"})
        self.assertEqual(r.status_code, 200, r.get_json())
        detail = r.get_json()
        self.assertTrue(detail["reevaluation_required"])
        self.assertEqual(detail["item"]["experience"], "가상 경험 10년")
        self.assertGreaterEqual(len(detail["revisions"]), 3)
        self.assertEqual(detail["past_evaluations"][0]["decision"], "approved")

    def test_profile_edit_rejects_status_change(self):
        eid = self._create()["id"]
        r = self.client.patch(f"/api/experts/{eid}", json={"expected_version": 1, "status": "approved"})
        self.assertEqual(r.status_code, 400)

    def test_upload_rejected_unless_approved(self):
        eid = self._create()["id"]
        body = {"title": "치수 확인 작업", "raw_text": "가상 원문"}
        r = self.client.post(f"/api/experts/{eid}/uploads", json=body)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["error"]["code"], "expert_not_approved")
        self._evaluate(eid, "rejected")
        self.assertEqual(self.client.post(f"/api/experts/{eid}/uploads", json=body).status_code, 403)

    def test_approved_upload_saved_unlinked_with_guidance(self):
        eid = self._create()["id"]
        self._evaluate(eid, "approved")
        r = self.client.post(f"/api/experts/{eid}/uploads", json={
            "title": "치수 확인 작업", "raw_text": "가상 원문", "evidence": "가상 근거", "synthetic": True})
        self.assertEqual(r.status_code, 201, r.get_json())
        body = r.get_json()
        self.assertEqual(body["item"]["status"], "uploaded_unlinked")
        self.assertIsNone(body["item"].get("catalog_id"))
        self.assertIn("S4", body["guidance"])
        detail = self.client.get(f"/api/experts/{eid}").get_json()
        self.assertEqual([w["id"] for w in detail["works"]], [body["item"]["id"]])
        links = self.client.get(f"/api/records/works/{body['item']['id']}").get_json()["links"]
        self.assertTrue(any(l["type"] == "experts" and l["id"] == eid for l in links))

    def test_upload_rejects_catalog_link_and_unknown_source(self):
        eid = self._create()["id"]
        self._evaluate(eid, "approved")
        r = self.client.post(f"/api/experts/{eid}/uploads", json={
            "title": "t", "raw_text": "r", "catalog_id": "cat_x"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post(f"/api/experts/{eid}/uploads", json={
            "title": "t", "raw_text": "r", "source_ids": ["mat_missing"]})
        self.assertEqual(r.status_code, 400)

    def test_unknown_expert_404(self):
        self.assertEqual(self.client.get("/api/experts/exp_nope").status_code, 404)

    def test_seed_approved_expert_can_upload(self):
        r = self.client.post("/api/experts/exp_seed01/uploads", json={"title": "t", "raw_text": "r"})
        self.assertEqual(r.status_code, 201, r.get_json())

    # F10.3 AI drafts (ai.attempt monkeypatched; no network)

    def _ai(self, eid, text, ok=True, error=None, adopted=True):
        run = {"attempted": True, "provider": "anthropic", "model": "claude-test", "ok": ok, "error": error,
               "started_at": "t0", "finished_at": "t1", "input_hash": "sha256:h", "usage": {"output_tokens": 2}}
        with mock.patch("backend.ai.adopted", return_value=adopted), \
                mock.patch("backend.ai.attempt", return_value=(text, run)):
            return self.client.post(f"/api/experts/{eid}/ai-draft", json={"expected_version": self._version(eid)})

    GOOD = json.dumps({"recruiting_post": "모집문", "experience_summary": "경험",
                       "evaluation_questions": ["질문1", "질문2"]}, ensure_ascii=False)

    def test_ai_draft_is_labeled_editable_and_never_changes_verdict(self):
        item = self._create()
        r = self._ai(item["id"], self.GOOD)
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(d["ai_draft"]["label"], "AI 초안 (claude-test)")
        self.assertFalse(d["ai_draft"]["outreach_sent"])
        self.assertFalse(d["ai_draft"]["expert_approval"])
        self.assertIn("실제 모집 연락", d["ai_draft"]["notice"])
        self.assertEqual(d["item"]["status"], "candidate")
        r = self.client.patch(f"/api/experts/{item['id']}/ai-draft", json={
            "expected_version": d["item"]["version"], "recruiting_post": "고친 모집문",
            "experience_summary": "고친 경험", "evaluation_questions": ["새 질문"]})
        self.assertEqual(r.status_code, 200, r.get_json())
        e = r.get_json()
        self.assertEqual(e["ai_draft"]["status"], "edited")
        self.assertEqual(e["ai_draft"]["content"]["recruiting_post"], "고친 모집문")
        self.assertEqual((e["item"]["status"], e["upload_allowed"]), ("candidate", False))

    def test_ai_off_failure_and_bad_json_are_recorded_without_draft(self):
        item = self._create()
        r = self._ai(item["id"], None, adopted=False)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"]["code"], "ai_not_adopted")
        d = self._ai(item["id"], None, ok=False, error="URLError: x").get_json()
        self.assertFalse(d["ai_result"]["ok"])
        self.assertIsNone(d["ai_draft"])
        d = self._ai(item["id"], "nope").get_json()
        self.assertEqual([a["error"] for a in d["ai_attempts"]], ["URLError: x", "unparsable_response"])
        self.assertEqual(self.client.patch(f"/api/experts/{item['id']}/ai-draft", json={
            "expected_version": d["item"]["version"], "recruiting_post": "a", "experience_summary": "b",
            "evaluation_questions": ["c"]}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
