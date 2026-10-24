"""Behavioral checks for the local review and delivery gates."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from backend.app import APIError, Application, create_server, fetch_public_url


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.app = Application(Path(__file__).resolve().parents[1], self.db)

    def tearDown(self):
        self.temp.cleanup()

    def ready_work(self, purpose="both"):
        if purpose != "both":
            self.app.create_or_update("works", {"license_purpose": purpose}, "work_demo_01")
        with patch("backend.app.call_ai", return_value=(None, "테스트: 규칙 기반")):
            self.app.refine_work("work_demo_01")
        return self.app.validate_work("work_demo_01", {"decision": "approved", "notes": "합성 관찰 단서·판단·결과 연결을 검토했습니다."})

    def generate(self, **kwargs):
        return self.app.generate_company({"name": "가상 검증회사", "work_id": "work_demo_01", "use_purpose": "evaluation", **kwargs})

    def test_bootstrap_persistence_and_relations(self):
        boot = self.app.bootstrap()
        self.assertEqual(len(boot["experts"]), 2)
        self.assertEqual(boot["settings"]["company_ax_fee"], 0)
        recreated = Application(Path(self.temp.name), self.db)
        self.assertEqual(len(recreated.bootstrap()["sources"]), 1)

    def test_unapproved_expert_cannot_upload(self):
        with self.assertRaises(APIError) as caught:
            self.app.create_or_update("works", {"title": "작업", "raw_text": "자료", "expert_id": "expert_demo_02", "catalog_id": "catalog_demo_01"})
        self.assertEqual(caught.exception.status, 403)

    def test_expert_cannot_bypass_review_via_patch(self):
        with self.assertRaises(APIError):
            self.app.create_or_update("experts", {"status": "approved"}, "expert_demo_02")

    def test_expert_review_requires_score_notes_and_experience(self):
        with self.assertRaises(APIError):
            self.app.evaluate_expert("expert_demo_02", {"decision": "approved", "score": 40, "notes": "심사"})
        expert = self.app.evaluate_expert("expert_demo_02", {"decision": "approved", "score": 84, "notes": "경력·도제 판단 사례를 사용자가 검토했습니다."})
        self.assertEqual(expert["status"], "approved")
        self.assertTrue(expert["evaluation"]["human_review"])
        changed = self.app.create_or_update("experts", {"experience": "수정된 경력"}, "expert_demo_02")
        self.assertEqual(changed["status"], "needs_review")

    def test_refinement_is_explicitly_rule_based(self):
        with patch("backend.app.call_ai", return_value=(None, "실제 AI 응답이 아닙니다.")):
            work = self.app.refine_work("work_demo_01")
        self.assertEqual(work["refined"]["mode"], "rule_based")
        self.assertFalse(work["refined"]["ai_used"])
        self.assertEqual(work["validation"]["decision"], "pending")

    def test_validation_and_generation_require_human_gate(self):
        with self.assertRaises(APIError):
            self.generate()
        with self.assertRaises(APIError):
            self.app.validate_work("work_demo_01", {"decision": "approved", "notes": "검토"})
        self.ready_work()
        company = self.generate()
        self.assertEqual(company["snapshot"]["provenance"]["human_validation"]["decision"], "approved")

    def test_evaluation_only_work_cannot_train(self):
        self.ready_work("evaluation")
        with self.assertRaises(APIError) as caught:
            self.generate(use_purpose="training")
        self.assertEqual(caught.exception.code, "PURPOSE_NOT_ALLOWED")
        self.assertEqual(self.generate()["use_purpose"], "evaluation")

    def test_training_allowed_when_rights_allow_both(self):
        self.ready_work()
        self.assertEqual(self.generate(use_purpose="training")["use_purpose"], "training")

    def test_original_change_requires_revalidation(self):
        self.ready_work()
        changed = self.app.create_or_update("works", {"raw_text": "검토가 필요한 변경 원문"}, "work_demo_01")
        self.assertIsNone(changed["refined"])
        self.assertEqual(changed["validation"]["decision"], "pending")
        with self.assertRaises(APIError):
            self.generate()

    def test_snapshot_survives_source_and_price_changes_and_runtime_reset(self):
        self.ready_work()
        company = self.generate(project_id="project_demo_01")
        snapshot = copy.deepcopy(company["snapshot"])
        digest = company["snapshot_sha256"]
        self.app.create_or_update("works", {"raw_text": "변경된 원문"}, "work_demo_01")
        self.app.create_or_update("projects", {"price": 50000}, "project_demo_01")
        self.app.run_company(company["id"], {"action": "request_inspection"})
        self.app.run_company(company["id"], {"action": "release"})
        reset = self.app.run_company(company["id"], {}, reset=True)
        self.assertEqual(reset["runtime"]["state"], "pending_review")
        self.assertEqual(reset["snapshot"], snapshot)
        self.assertEqual(reset["snapshot_sha256"], digest)

    def test_release_without_inspection_is_blocked(self):
        self.ready_work()
        company = self.generate()
        with self.assertRaises(APIError) as caught:
            self.app.run_company(company["id"], {"action": "release"})
        self.assertEqual(caught.exception.code, "INSPECTION_REQUIRED")
        blocked = self.app.store.get("companies", company["id"])
        self.assertEqual(blocked["runtime"]["history"][-1]["result"], "blocked")
        self.app.run_company(company["id"], {"action": "hold"})
        self.app.run_company(company["id"], {"action": "request_inspection"})
        released = self.app.run_company(company["id"], {"action": "release"})
        self.assertEqual(released["runtime"]["score"], 100)
        with self.assertRaises(APIError):
            self.app.run_company(company["id"], {"action": "hold"})

    def test_free_company_ax_cannot_be_changed(self):
        with self.assertRaises(APIError) as caught:
            self.app.create_or_update("projects", {"company_ax_fee": 10}, "project_demo_01")
        self.assertEqual(caught.exception.code, "FREE_AX_REQUIRED")

    def test_three_feedback_paths_are_persistent(self):
        for kind in ("sample_review", "pilot", "topic"):
            self.app.create_or_update("feedback", {"kind": kind, "message": "후속 협의 요청", "name": "가상 구매자"})
        self.assertEqual(len(self.app.bootstrap()["feedback"]), 3)

    def test_private_and_non_http_collection_are_blocked(self):
        for url in ("http://127.0.0.1/", "http://169.254.169.254/", "http://[::1]/", "file:///etc/passwd", "https://example.com:22/"):
            with self.subTest(url=url), self.assertRaises(APIError):
                fetch_public_url(url)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        (root / "static").mkdir()
        (root / "static" / "index.html").write_text("<h1>Prototype</h1>")
        cls.server = create_server(root, root / "test.sqlite3", 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def request(self, path, data=None, headers=None):
        payload = json.dumps(data).encode() if data is not None else None
        request = Request(self.base + path, data=payload, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urlopen(request) as response:
                return response.status, response.read(), dict(response.headers)
        except HTTPError as error:
            return error.code, error.read(), dict(error.headers)

    def test_http_bootstrap_and_static(self):
        status, raw, headers = self.request("/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertIsInstance(json.loads(raw)["sources"], list)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/.env")[0], 403)

    def test_cross_origin_mutation_rejected(self):
        status, raw, _ = self.request("/api/feedback", {"kind": "topic", "message": "test"}, {"Origin": "https://other.example"})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(raw)["code"], "ORIGIN_BLOCKED")

    def test_company_export_is_immutable_package(self):
        app = self.server.app
        with patch("backend.app.call_ai", return_value=(None, "test")):
            app.refine_work("work_demo_01")
        app.validate_work("work_demo_01", {"decision": "approved", "notes": "검토 완료"})
        company = app.generate_company({"name": "테스트", "work_id": "work_demo_01", "use_purpose": "evaluation"})
        status, raw, headers = self.request(f"/api/companies/{company['id']}/export")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(json.loads(raw)["package"], company["snapshot"])
        self.assertNotIn("runtime", json.loads(raw))


if __name__ == "__main__":
    unittest.main()
