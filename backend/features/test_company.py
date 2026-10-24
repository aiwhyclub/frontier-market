import tempfile
import unittest
from pathlib import Path

from backend import db
from backend.app import create_app
from backend.features import company

ROOT = Path(__file__).resolve().parent.parent.parent


class CompanyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(ROOT, Path(self.tmp.name) / "company.sqlite3")
        if "company" not in self.app.blueprints:
            self.app.register_blueprint(company.bp)
        self.client = self.app.test_client()

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


if __name__ == "__main__":
    unittest.main()
