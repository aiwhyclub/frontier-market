import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend import db
from backend.app import create_app
from backend.features import ai_fakes, company

ROOT = Path(__file__).resolve().parent.parent.parent


class _CompanyBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(ROOT, Path(self.tmp.name) / "company.sqlite3")
        if "company" not in self.app.blueprints:
            self.app.register_blueprint(company.bp)
        self.client = self.app.test_client()
        self.client.environ_base["HTTP_X_FM_LANG"] = "ko"  # these tests assert the Korean message text

    def tearDown(self):
        self.tmp.cleanup()

    def _body(self, **over):
        body = {"name": "가상 정밀기업 시험", "work_ids": ["wrk_seed01"], "catalog_id": "cat_seed01",
                "project_id": "prj_seed01", "purpose": "training"}
        body.update(over)
        return body

    def _supplement_all(self, purpose_list=("training", "evaluation")):
        for stype, sid in (("works", "wrk_seed01"), ("experts", "exp_seed01")):
            r = self.client.post("/api/company/rights", json={
                "subject_type": stype, "subject_id": sid, "purposes": list(purpose_list),
                "evidence": "합성 확인 근거", "confirmed_by": "가상 운영자"})
            self.assertEqual(r.status_code, 201, r.get_json())



class CompanyTest(_CompanyBase):
    def test_candidates_and_seed_verified(self):
        data = self.client.get("/api/company/candidates").get_json()
        works = {w["id"]: w for w in data["works"]}
        self.assertTrue(works["wrk_seed01"]["verified"])
        self.assertEqual(data["catalogs"][0]["id"], "cat_seed01")

    def test_missing_project_and_unverified_work_block_without_saving(self):
        r = self.client.post("/api/company/companies", json=self._body(project_id=None))
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/api/company/companies", json=self._body(work_ids=["wrk_seed02", "nope"]))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.client.get("/api/company/companies").get_json()["total"], 1)  # seed co_seed01 only

    def test_rights_hold_then_supplement_then_create_and_export(self):
        r = self.client.post("/api/company/companies", json=self._body())
        self.assertEqual(r.status_code, 409)
        holds = r.get_json()["holds"]
        self.assertTrue(any(h["target_id"] == "exp_seed01" for h in holds))
        self.assertEqual(self.client.get("/api/company/companies").get_json()["total"], 1)  # seed co_seed01 only

        self._supplement_all(("training",))
        r = self.client.post("/api/company/companies", json=self._body())
        self.assertEqual(r.status_code, 201, r.get_json())
        cid = r.get_json()["id"]

        detail = self.client.get(f"/api/company/companies/{cid}").get_json()
        self.assertEqual(len(detail["package"]["sha256"]), 64)
        self.assertTrue(detail["current_status"]["allowed"])
        self.assertEqual(len(detail["package"]["snapshot"]["samples"]["training"]), 1)
        self.assertEqual(detail["package"]["snapshot"]["samples"]["evaluation"], [])

        ex = self.client.get(f"/api/company/companies/{cid}/export")
        self.assertEqual(ex.status_code, 200)

    def test_evaluation_check_passes_with_training_supplement(self):
        self._supplement_all(("training", "evaluation"))
        r = self.client.post("/api/company/companies", json=self._body(work_ids=["wrk_seed01"]))
        self.assertEqual(r.status_code, 201)
        # Source mat_seed01 is learning-allowed; eval-only rights exist for mat_seed02 only.
        check = self.client.post("/api/company/check", json=self._body(purpose="evaluation")).get_json()
        self.assertTrue(check["allowed"], check)

    def test_revoke_past_approval_holds_export_and_new_creation(self):
        self._supplement_all(("training",))
        cid = self.client.post("/api/company/companies", json=self._body()).get_json()["id"]
        r = self.client.post("/api/company/approvals/revoke", json={
            "subject_type": "works", "subject_id": "wrk_seed01", "subject_version": 1,
            "reason": "검토 오류", "evidence": "검토 기록", "actor": "지정 운영책임자"})
        self.assertEqual(r.status_code, 201)
        ex = self.client.get(f"/api/company/companies/{cid}/export")
        self.assertEqual(ex.status_code, 409)
        self.assertEqual(ex.get_json()["error"]["code"], "hold")
        self.assertEqual(self.client.get(f"/api/company/companies/{cid}").status_code, 200)

    def test_task_run_release_before_inspection_rejected_and_reset_keeps_history(self):
        self._supplement_all(("training",))
        cid = self.client.post("/api/company/companies", json=self._body()).get_json()["id"]
        tid = "tsk_wrk_seed01"
        r = self.client.post(f"/api/company/companies/{cid}/tasks/{tid}/action",
                             json={"action": "release", "expected_run_version": 1})
        self.assertEqual(r.status_code, 409)
        r = self.client.post(f"/api/company/companies/{cid}/tasks/{tid}/action",
                             json={"action": "request_inspection", "expected_run_version": 1})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["run"]["score"], 4)
        r = self.client.post(f"/api/company/companies/{cid}/tasks/{tid}/action",
                             json={"action": "release", "expected_run_version": 2})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(f"/api/company/companies/{cid}/tasks/{tid}/reset",
                             json={"expected_run_version": 3})
        self.assertEqual(r.status_code, 200)
        run = r.get_json()["run"]
        self.assertEqual((run["state"], run["attempt"]), ("pending_review", 2))
        runs = self.client.get(f"/api/company/companies/{cid}/runs").get_json()
        events = [h["event"] for h in runs["history"]]
        self.assertIn("release_blocked", events)
        self.assertIn("reset", events)
        detail = self.client.get(f"/api/company/companies/{cid}").get_json()
        self.assertTrue(detail["package"]["sha256"])


CASE = {"document_title": "가상 학습 사례: 치수 확인 순서", "document_body": "치수를 먼저 재고 기준과 비교한다.",
        "learning_case": "초보자는 순서를 건너뛰어 오판했다."}


class CompanyAiDraftTest(_CompanyBase):

    def _runs(self):
        with closing(db.connect(self.app.config["DB_PATH"])) as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM ai_draft_runs ORDER BY created_at").fetchall()]

    def _companies(self):
        return self.client.get("/api/company/companies").get_json()["total"]

    @unittest.skipIf(ai_fakes.REAL, "needs fake")
    def test_rights_hold_blocks_draft_before_any_ai_call(self):
        calls = ai_fakes.install(self, CASE)
        r = self.client.post("/api/company/ai-draft", json=self._body())
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.get_json()["holds"])
        self.assertEqual(calls, [])
        self.assertEqual(self._runs(), [])
        r = self.client.post("/api/company/ai-draft", json=self._body(project_id=None))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(calls, [])

    @unittest.skipIf(ai_fakes.REAL, "needs fake")
    def test_draft_then_adopt_passes_gate_and_lands_labelled_in_snapshot(self):
        self._supplement_all(("training",))
        calls = ai_fakes.install(self, CASE)
        before = self._companies()
        r = self.client.post("/api/company/ai-draft", json=self._body())
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(d["draft"]["document_title"], CASE["document_title"])
        self.assertEqual(d["ai"]["label"], "AI 초안 (claude-test-1)")
        self.assertEqual(calls[0]["feature"], "F10.6")
        self.assertEqual(self._companies(), before)  # draft alone creates nothing
        run = self._runs()[0]
        self.assertEqual((run["provider"], run["ok"]), ("anthropic", 1))
        edited = dict(d["draft"], learning_case="사람이 고친 학습 사례")
        r = self.client.post(f"/api/company/ai-draft/{d['ai']['run_id']}/adopt",
                             json={**self._body(), "draft": edited})
        self.assertEqual(r.status_code, 201, r.get_json())
        cid = r.get_json()["id"]
        pkg = self.client.get(f"/api/company/companies/{cid}").get_json()["package"]["snapshot"]
        item = pkg["ai_drafts"][0]
        self.assertEqual(item["learning_case"], "사람이 고친 학습 사례")
        self.assertEqual(item["label"], "AI 초안 (claude-test-1)")
        self.assertFalse(item["model_training_performed"])
        self.assertIn("실제 모델 훈련", item["note"])
        self.assertEqual(self.client.get(f"/api/company/companies/{cid}/export").status_code, 200)
        again = self.client.post(f"/api/company/ai-draft/{d['ai']['run_id']}/adopt",
                                 json={**self._body(), "draft": edited})
        self.assertEqual(again.status_code, 409)

    @unittest.skipIf(ai_fakes.REAL, "needs fake")
    def test_adopt_rechecks_gate_when_rights_vanish(self):
        self._supplement_all(("training",))
        ai_fakes.install(self, CASE)
        d = self.client.post("/api/company/ai-draft", json=self._body()).get_json()
        before = self._companies()
        r = self.client.post(f"/api/company/ai-draft/{d['ai']['run_id']}/adopt",
                             json={**self._body(purpose="evaluation"), "draft": d["draft"]})
        self.assertEqual(r.status_code, 409)  # training-only rights do not cover evaluation
        self.assertEqual(self._companies(), before)
        self.assertIsNone(self._runs()[0]["adopted_at"])

    @unittest.skipIf(ai_fakes.REAL, "needs fake")
    def test_ai_failure_falls_back_to_manual_creation(self):
        self._supplement_all(("training",))
        ai_fakes.install(self, None)
        d = self.client.post("/api/company/ai-draft", json=self._body()).get_json()
        self.assertIsNone(d["draft"])
        self.assertEqual(d["ai"]["label"], "수동 작성")
        self.assertEqual(self._runs()[0]["ok"], 0)
        self.assertEqual(self.client.post("/api/company/companies", json=self._body()).status_code, 201)


if __name__ == "__main__":
    unittest.main()
