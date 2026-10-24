import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.app import create_app
from backend import db
from backend.features import ai_fakes, fde

ROOT = Path(__file__).resolve().parent.parent.parent


class _FdeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(ROOT, Path(self.tmp.name) / "records.sqlite3")
        self.client = self.app.test_client()
        self.client.environ_base["HTTP_X_FM_LANG"] = "ko"  # these tests assert the Korean message text

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



class FdeApiTest(_FdeBase):
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


PROPOSAL = {"scope": "가상 범위 제안: 검수 절차 정리", "training": "가상 교육 제안: 2시간 실습",
            "tools": "가상 도구 제안: 체크리스트 양식"}


class FdeAiDraftAndPnlTest(_FdeBase):
    def _pid(self):
        return self._project_id()

    def _runs(self):
        with closing(db.connect(self.app.config["DB_PATH"])) as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM ai_draft_runs ORDER BY created_at").fetchall()]

    # F10.5
    @unittest.skipIf(ai_fakes.REAL, "needs fake")
    def test_proposal_is_draft_then_adopted_with_edit_and_never_actual(self):
        calls = ai_fakes.install(self, PROPOSAL)
        pid = self._pid()
        r = self.client.post(f"/api/fde/projects/{pid}/ai-draft")
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(d["draft"]["scope"], PROPOSAL["scope"])
        self.assertEqual(d["ai"]["label"], "AI 초안 (claude-test-1)")
        self.assertEqual(calls[0]["feature"], "F10.5")
        item = self.client.get(f"/api/fde/projects/{pid}").get_json()["item"]
        self.assertNotIn("ai_proposal", item["fields"])  # not saved yet
        edited = dict(d["draft"], tools="사람이 고친 도구 제안")
        r = self.client.post(f"/api/fde/projects/{pid}/ai-draft/{d['ai']['run_id']}/adopt",
                             json=dict(edited, expected_version=1))
        self.assertEqual(r.status_code, 200, r.get_json())
        f = r.get_json()["item"]["fields"]
        self.assertEqual(f["ai_proposal"]["tools"], "사람이 고친 도구 제안")
        self.assertEqual(f["ai_proposal"]["label"], "AI 초안 (claude-test-1)")
        self.assertIn("실제 현장 설치", f["ai_proposal"]["note"])
        self.assertEqual(f["stage_log"], [])  # not recorded as a stage / actual result
        self.assertEqual(f["actual_result"], "")
        self.assertEqual(f["scope"], "가상 범위")  # manual scope untouched
        self.assertEqual(f["company_ax_fee"]["amount"], 0)
        run = self._runs()[0]
        self.assertEqual((run["provider"], run["ok"], run["feature"]), ("anthropic", 1, "F10.5"))
        again = self.client.post(f"/api/fde/projects/{pid}/ai-draft/{d['ai']['run_id']}/adopt",
                                 json=dict(edited, expected_version=2))
        self.assertEqual(again.status_code, 409)

    @unittest.skipIf(ai_fakes.REAL, "needs fake")
    def test_proposal_failure_keeps_manual_and_logs_error(self):
        ai_fakes.install(self, None)
        pid = self._pid()
        d = self.client.post(f"/api/fde/projects/{pid}/ai-draft").get_json()
        self.assertIsNone(d["draft"])
        self.assertEqual(d["ai"]["label"], "수동 작성")
        self.assertEqual(self._runs()[0]["ok"], 0)
        r = self.client.post(f"/api/fde/projects/{pid}/ai-draft/{d['ai']['run_id']}/adopt",
                             json=dict(PROPOSAL, expected_version=1))
        self.assertEqual(r.status_code, 404)

    def test_proposal_for_missing_project_404(self):
        ai_fakes.install(self, PROPOSAL)
        self.assertEqual(self.client.post("/api/fde/projects/prj_nope/ai-draft").status_code, 404)

    # F6.5
    def _pnl(self, pid, version=1, **over):
        body = {"expected_version": version, "currency": "KRW", "price": "1200000", "cost": "800000",
                "unit": "프로젝트 1건"}
        body.update(over)
        return self.client.put(f"/api/fde/projects/{pid}/pnl", json=body)

    def test_pnl_auto_calculation_labelled_assumed_and_separate_from_ax_zero(self):
        pid = self._pid()
        r = self._pnl(pid)
        self.assertEqual(r.status_code, 200, r.get_json())
        f = r.get_json()["item"]["fields"]
        pnl = f["pnl"]
        self.assertEqual((pnl["price"], pnl["cost"], pnl["margin"], pnl["margin_rate"]),
                         ("1200000", "800000", "400000", "33.33"))
        self.assertEqual((pnl["currency"], pnl["unit"]), ("KRW", "프로젝트 1건"))
        self.assertTrue(pnl["label"].startswith("가정 손익"))
        self.assertIn("기업 AX 0원", pnl["note"])
        self.assertEqual(f["company_ax_fee"]["amount"], 0)
        self.assertEqual(f["company_ax_fee_minor"], 0)
        self.assertEqual(f["actual_result"], "")

    def test_zero_price_margin_rate_unavailable(self):
        pid = self._pid()
        pnl = self._pnl(pid, price="0", cost="5000").get_json()["item"]["fields"]["pnl"]
        self.assertIsNone(pnl["margin_rate"])
        self.assertEqual(pnl["margin_rate_label"], "계산 불가")
        self.assertEqual(pnl["margin"], "-5000")

    def test_usd_scale_and_rounding(self):
        pid = self._pid()
        pnl = self._pnl(pid, currency="USD", price="12000.00", cost="9000.5").get_json()["item"]["fields"]["pnl"]
        self.assertEqual((pnl["price"], pnl["cost"], pnl["margin"]), ("12000.00", "9000.50", "2999.50"))
        self.assertEqual(pnl["margin_rate"], "25.00")
        with closing(db.connect(self.app.config["DB_PATH"])) as conn:
            stored = db.get_record(conn, "projects", pid)
        self.assertEqual((stored["pnl"]["price_minor"], stored["pnl"]["cost_minor"]), (1200000, 900050))

    def test_pnl_rejects_bad_numbers_currency_and_ax_confusion(self):
        pid = self._pid()
        for over in ({"price": 1200000}, {"price": 12.5}, {"price": "1e3"}, {"price": "-1"},
                     {"price": "1.5"}, {"cost": "abc"}, {"currency": "XXX"}, {"currency": "krw"},
                     {"price": "9007199254740992"}, {"unit": ""}):
            r = self._pnl(pid, **over)
            self.assertEqual(r.status_code, 422, over)
        self.assertEqual(self._pnl(pid, company_ax_fee="1000").status_code, 422)
        self.assertEqual(self._pnl(pid, version=9).status_code, 409)
        self.assertEqual(self._pnl("prj_nope").status_code, 404)

    def test_pnl_optional_and_clearable(self):
        pid = self._pid()
        item = self.client.get(f"/api/fde/projects/{pid}").get_json()["item"]
        self.assertIsNone(item["fields"]["pnl"])
        self._pnl(pid)
        r = self.client.delete(f"/api/fde/projects/{pid}/pnl", json={"expected_version": 2})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.get_json()["item"]["fields"]["pnl"])


if __name__ == "__main__":
    unittest.main()
