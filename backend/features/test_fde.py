import tempfile
import unittest
from pathlib import Path

from backend.app import create_app
from backend.features import fde

ROOT = Path(__file__).resolve().parent.parent.parent


class FdeApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(ROOT, Path(self.tmp.name) / "records.sqlite3")
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def _create(self, **overrides):
        payload = {
            "title": "가상 FDE 프로젝트", "synthetic": True, "catalog_id": "cat_seed01",
            "work_ids": ["wrk_seed01"], "expert_ids": ["exp_seed01"], "company_name": "가상 기업",
            "objective": "가상 목표", "scope": "가상 범위", "deliverables": "가상 산출물",
            "owner": "가상 운영자", "notes": "메모", "site_name": "가상 현장",
            "planned_start": "2026-11-01", "planned_end": "2026-11-30",
        }
        payload.update(overrides)
        return self.client.post("/api/fde/projects", json=payload)

    def _project_id(self):
        resp = self._create()
        self.assertEqual(resp.status_code, 201, resp.get_json())
        return resp.get_json()["item"]["id"]

    def test_create_links_and_reopen(self):
        pid = self._project_id()
        data = self.client.get(f"/api/fde/projects/{pid}").get_json()
        self.assertEqual(data["item"]["fields"]["catalog_id"], "cat_seed01")
        self.assertEqual(data["item"]["fields"]["company_ax_fee"]["amount"], 0)
        self.assertTrue(data["item"]["synthetic"])
        self.assertTrue(any(l["id"] == "cat_seed01" for l in data["links"]))
        listing = self.client.get("/api/fde/projects").get_json()
        self.assertIn(pid, [i["id"] for i in listing["items"]])

    def test_seed_project_is_reopened(self):
        data = self.client.get("/api/fde/projects/prj_seed01").get_json()
        self.assertEqual(data["item"]["id"], "prj_seed01")

    def test_create_rejects_missing_catalog_and_unlinked_work(self):
        resp = self._create(catalog_id="cat_missing")
        self.assertEqual(resp.status_code, 422)
        self.assertIn("catalog_id", resp.get_json()["error"]["fields"])
        resp = self._create(work_ids=["wrk_missing"])
        self.assertEqual(resp.status_code, 422)
        self.assertIn("work_ids", resp.get_json()["error"]["fields"])
        resp = self._create(work_ids=["wrk_seed02"])
        self.assertEqual(resp.status_code, 422)
        self.assertIn("work_ids", resp.get_json()["error"]["fields"])

    def test_create_requires_synthetic_and_at_least_one_work(self):
        self.assertEqual(self._create(synthetic=False).status_code, 422)
        self.assertEqual(self._create(work_ids=[]).status_code, 422)

    def test_nonzero_ax_fee_is_rejected_and_zero_accepted(self):
        resp = self._create(company_ax_fee="1200000")
        self.assertEqual(resp.status_code, 422)
        self.assertIn("company_ax_fee", resp.get_json()["error"]["fields"])
        self.assertEqual(self._create(company_ax_fee=0).status_code, 201)
        self.assertEqual(self._create(company_ax_fee="0원").status_code, 201)

    def test_price_and_cost_fields_are_refused(self):
        self.assertEqual(self._create(price_minor=1000).status_code, 422)

    def test_bad_dates_rejected(self):
        resp = self._create(planned_start="2026-12-01", planned_end="2026-11-01")
        self.assertEqual(resp.status_code, 422)

    def test_patch_edits_scope_fields_with_version(self):
        pid = self._project_id()
        resp = self.client.patch(f"/api/fde/projects/{pid}",
                                 json={"expected_version": 1, "objective": "새 목표", "owner": "새 담당"})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        item = self.client.get(f"/api/fde/projects/{pid}").get_json()["item"]
        self.assertEqual(item["fields"]["objective"], "새 목표")
        self.assertEqual(item["fields"]["owner"], "새 담당")
        self.assertEqual(item["fields"]["scope"], "가상 범위")
        self.assertEqual(item["version"], 2)
        stale = self.client.patch(f"/api/fde/projects/{pid}", json={"expected_version": 1, "scope": "x"})
        self.assertEqual(stale.status_code, 409)

    def test_patch_rejects_ax_fee_and_unknown_fields(self):
        pid = self._project_id()
        r = self.client.patch(f"/api/fde/projects/{pid}", json={"expected_version": 1, "company_ax_fee": 500})
        self.assertEqual(r.status_code, 422)
        r = self.client.patch(f"/api/fde/projects/{pid}", json={"expected_version": 1, "price": 5})
        self.assertEqual(r.status_code, 422)

    def test_stage_records_basis_and_evidence(self):
        pid = self._project_id()
        r = self.client.post(f"/api/fde/projects/{pid}/stages",
                             json={"expected_version": 1, "kind": "plan", "basis": "planned",
                                   "note": "계획 수립"})
        self.assertEqual(r.status_code, 201, r.get_json())
        r = self.client.post(f"/api/fde/projects/{pid}/stages",
                             json={"expected_version": 2, "kind": "progress", "basis": "synthetic",
                                   "note": "합성 진행", "evidence": "가상 기록 1"})
        self.assertEqual(r.status_code, 201, r.get_json())
        r = self.client.post(f"/api/fde/projects/{pid}/stages",
                             json={"expected_version": 3, "kind": "site_result", "basis": "actual",
                                   "note": "현장 결과"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("evidence", r.get_json()["error"]["fields"])
        item = self.client.get(f"/api/fde/projects/{pid}").get_json()["item"]
        log = item["fields"]["stage_log"]
        self.assertEqual([e["basis"] for e in log], ["planned", "synthetic"])
        self.assertEqual(item["fields"]["stage"], "progress")

    def test_plan_cannot_claim_actual_basis(self):
        pid = self._project_id()
        r = self.client.post(f"/api/fde/projects/{pid}/stages",
                             json={"expected_version": 1, "kind": "plan", "basis": "actual", "note": "x"})
        self.assertEqual(r.status_code, 422)

    def test_options_lists_references(self):
        data = self.client.get("/api/fde/options").get_json()["options"]
        self.assertIn("cat_seed01", [i["id"] for i in data["catalog"]])
        self.assertIn("wrk_seed01", [i["id"] for i in data["works"]])
        self.assertIn("exp_seed01", [i["id"] for i in data["experts"]])


if __name__ == "__main__":
    unittest.main()
