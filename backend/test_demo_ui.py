"""EPIC 13 visitor UI: public flag endpoint, static wiring, operator login with CSRF, local mode unchanged."""
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from backend.app import create_app
from backend.i18n_testutil import tk
from backend.test_public import END, ENV, OP_TOKEN, ROOT, Clock, PublicBase


class StatusTest(PublicBase):
    def test_status_is_anonymous_and_reports_public_mode(self):
        r = self.app.test_client().get("/api/demo/status")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["public"])
        self.assertFalse(body["closed"])
        self.assertEqual(body["public_end"], "2026-10-24T12:00:00Z")
        self.assertNotIn(OP_TOKEN, r.get_data(as_text=True))

    def test_status_after_end_is_410_closed_for_visitors_and_operator(self):
        self.clock.at = END
        for client in (self.app.test_client(), self.op):
            r = client.get("/api/demo/status")
            self.assertEqual(r.status_code, 410)
            self.assertEqual(r.get_json()["error"]["code"], "closed")

    def test_operator_api_still_401_anonymously_and_session_endpoint_guides_login(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/api/records/works").status_code, 401)
        self.assertEqual(client.get("/api/auth/session").status_code, 401)

    def test_operator_session_returns_csrf_that_authorizes_mutations(self):
        client = self.app.test_client()
        r = client.post("/api/auth/login", json={"token": OP_TOKEN})
        self.assertEqual(r.status_code, 200)
        csrf = client.get("/api/auth/session").get_json()["csrf_token"]
        self.assertEqual(csrf, r.get_json()["csrf_token"])
        body = {"subject_type": "works", "subject_id": "wrk_seed01", "purposes": ["training"],
                "evidence": "합성 확인 근거", "confirmed_by": "가상 운영자"}
        self.assertEqual(client.post("/api/company/rights", json=body).status_code, 403)
        self.assertEqual(client.post("/api/company/rights", json=body, headers={"X-CSRF-Token": csrf}).status_code, 201)

    def test_bad_token_login_is_401(self):
        r = self.app.test_client().post("/api/auth/login", json={"token": "wrong"})
        self.assertEqual(r.status_code, 401)


class StaticWiringTest(PublicBase):
    def test_index_loads_demo_js_before_app_js_and_both_are_served(self):
        client = self.app.test_client()
        html = client.get("/").get_data(as_text=True)
        self.assertLess(html.index("/static/demo.js"), html.index("/static/app.js"))
        js = client.get("/static/demo.js")
        self.assertEqual(js.status_code, 200)
        text = js.get_data(as_text=True)
        self.assertIn("/api/demo/status", text)
        self.assertIn("/api/auth/login", text)
        self.assertIn("X-CSRF-Token", text)

    def test_visitor_layer_calls_only_demo_endpoints_for_data(self):
        text = (ROOT / "static" / "demo.js").read_text(encoding="utf-8")
        for forbidden in ("/api/records", "/api/company", "/api/rights", "/api/collect", "/api/health"):
            self.assertNotIn(forbidden, text)

    def test_end_notice_text_and_touch_targets_present(self):
        text = (ROOT / "static" / "demo.js").read_text(encoding="utf-8")
        self.assertIn(tk("공개 시연이 종료되었습니다"), text)
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".fm-demo-input{min-height:44px", css)

    def test_app_js_waits_for_boot_promise(self):
        text = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("window.FMBoot", text)


class LocalModeTest(unittest.TestCase):
    def test_local_mode_has_no_demo_status_and_operator_api_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(ROOT, Path(tmp) / "local.sqlite3", env={})
            client = app.test_client()
            self.assertEqual(client.get("/api/demo/status").status_code, 404)
            self.assertEqual(client.get("/api/auth/session").status_code, 404)
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/api/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()
