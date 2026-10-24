"""EPIC 13.3: public demo mode - end-time boundary, visitor isolation, session/CSRF, operator-only."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app import create_app

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))
END = datetime(2026, 10, 24, 21, 0, 0, tzinfo=KST)  # == 2026-10-24T12:00:00Z
OP_TOKEN = "operator-token-0123456789abcdef"
ENV = {"FM_PUBLIC_MODE": "1", "FM_SECRET_KEY": "s" * 40, "OPERATOR_TOKEN": OP_TOKEN,
       "FM_PUBLIC_END": "2026-10-24T12:00:00Z", "FM_COOKIE_SECURE": "0"}


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


class PublicBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock(END - timedelta(hours=2))
        self.app = create_app(ROOT, Path(self.tmp.name) / "pub.sqlite3", env=dict(ENV), clock=self.clock)
        self.op = self.app.test_client()
        self.op_csrf = self.login(self.op)
        self.cid = self.make_company()

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, client, token=OP_TOKEN):
        r = client.post("/api/auth/login", json={"token": token})
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()["csrf_token"]

    def opost(self, path, body, csrf=None):
        return self.op.post(path, json=body, headers={"X-CSRF-Token": csrf or self.op_csrf})

    def make_company(self):
        for stype, sid in (("works", "wrk_seed01"), ("experts", "exp_seed01")):
            r = self.opost("/api/company/rights", {
                "subject_type": stype, "subject_id": sid, "purposes": ["training", "evaluation"],
                "evidence": "합성 확인 근거", "confirmed_by": "가상 운영자"})
            self.assertEqual(r.status_code, 201, r.get_json())
        r = self.opost("/api/company/companies", {
            "name": "공개 시연 회사", "work_ids": ["wrk_seed01"], "catalog_id": "cat_seed01",
            "project_id": "prj_seed01", "purpose": "training"})
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["id"]

    def visitor(self):
        client = self.app.test_client()
        r = client.get("/api/demo/bootstrap")
        self.assertEqual(r.status_code, 200)
        return client, r.get_json()["csrf_token"]

    def vpost(self, client, csrf, path, body, headers=None):
        return client.post(path, json=body, headers={"X-CSRF-Token": csrf, **(headers or {})})

    def task_id(self, client):
        view = client.get(f"/api/demo/companies/{self.cid}").get_json()["item"]
        return view["tasks"][0]["task_id"]


class BoundaryTest(PublicBase):
    def test_20_59_59_open_21_00_00_and_after_closed(self):
        client, csrf = self.visitor()
        tid = self.task_id(client)
        cases = [(END - timedelta(seconds=1), 200), (END, 410), (END + timedelta(seconds=1), 410)]
        for at, expected in cases:
            self.clock.at = at
            self.assertEqual(client.get("/api/demo/bootstrap").status_code, expected, at)
            self.assertEqual(client.get("/").status_code, expected, at)
            self.assertEqual(client.get("/static/styles.css").status_code, expected, at)

    def test_old_visitor_session_cannot_run_after_end_and_nothing_is_written(self):
        client, csrf = self.visitor()
        tid = self.task_id(client)
        self.clock.at = END
        r = self.vpost(client, csrf, f"/api/demo/companies/{self.cid}/run",
                       {"task_id": tid, "action": "request_inspection", "expected_run_version": 0},
                       {"Idempotency-Key": "closed-key-0001"})
        self.assertEqual(r.status_code, 410)
        # a past receipt cannot be re-requested either
        self.clock.at = END - timedelta(hours=1)
        ok = self.vpost(client, csrf, f"/api/demo/companies/{self.cid}/run",
                        {"task_id": tid, "action": "request_inspection", "expected_run_version": 0},
                        {"Idempotency-Key": "open-key-00001"})
        self.assertEqual(ok.status_code, 200)
        self.clock.at = END + timedelta(seconds=1)
        again = self.vpost(client, csrf, f"/api/demo/companies/{self.cid}/run",
                           {"task_id": tid, "action": "request_inspection", "expected_run_version": 0},
                           {"Idempotency-Key": "open-key-00001"})
        self.assertEqual(again.status_code, 410)

    def test_operator_keeps_access_after_end_and_state_is_preserved(self):
        client, csrf = self.visitor()
        tid = self.task_id(client)
        self.vpost(client, csrf, f"/api/demo/companies/{self.cid}/run",
                   {"task_id": tid, "action": "hold", "expected_run_version": 0})
        self.clock.at = END + timedelta(hours=5)
        self.assertEqual(self.op.get("/api/company/companies").status_code, 200)
        self.assertEqual(self.op.get("/").status_code, 200)
        self.assertEqual(self.op.get("/api/demo/bootstrap").status_code, 410)  # public prefix closed for all
        self.assertEqual(self.app.test_client().get("/").status_code, 410)

    def test_clock_cannot_be_injected_by_request(self):
        client, _ = self.visitor()
        self.clock.at = END + timedelta(seconds=1)
        for headers in ({"Date": "Sat, 24 Oct 2026 11:00:00 GMT"}, {"X-Fm-Now": "2026-01-01T00:00:00Z"}):
            self.assertEqual(client.get("/api/demo/bootstrap", headers=headers).status_code, 410)
        self.assertEqual(client.get("/api/demo/bootstrap?now=2026-01-01T00:00:00Z&clock=1").status_code, 410)


class IsolationTest(PublicBase):
    def run_action(self, client, csrf, tid, action, version, key=None):
        headers = {"Idempotency-Key": key} if key else None
        return self.vpost(client, csrf, f"/api/demo/companies/{self.cid}/run",
                          {"task_id": tid, "action": action, "expected_run_version": version}, headers)

    def view(self, client):
        return client.get(f"/api/demo/companies/{self.cid}").get_json()["item"]["tasks"][0]

    def test_unstarted_is_version_zero_first_run_is_one_then_monotonic(self):
        a, ca = self.visitor()
        tid = self.task_id(a)
        task = self.view(a)
        self.assertEqual((task["run"]["run_version"], task["history"]), (0, []))
        r = self.run_action(a, ca, tid, "request_inspection", 0)
        self.assertEqual(r.get_json()["run"]["run_version"], 1)
        self.assertEqual(self.run_action(a, ca, tid, "hold", 1).get_json()["run"]["run_version"], 2)
        reset = self.vpost(a, ca, f"/api/demo/companies/{self.cid}/reset",
                           {"task_id": tid, "expected_run_version": 2})
        run = reset.get_json()["run"]
        self.assertEqual((run["run_version"], run["attempt"], run["state"]), (3, 2, "pending_review"))
        self.assertEqual(len(self.view(a)["history"]), 3)

    def test_visitor_a_and_b_and_operator_do_not_affect_each_other(self):
        a, ca = self.visitor()
        b, cb = self.visitor()
        tid = self.task_id(a)
        op_before = self.op.get(f"/api/company/companies/{self.cid}").get_json()
        self.run_action(a, ca, tid, "request_inspection", 0)
        self.run_action(a, ca, tid, "hold", 1)
        tb = self.view(b)
        self.assertEqual((tb["run"]["run_version"], tb["history"]), (0, []))
        # B starts at version 0 regardless of A, and A's version is not usable by B
        self.assertEqual(self.run_action(b, cb, tid, "hold", 2).status_code, 409)
        self.assertEqual(self.run_action(b, cb, tid, "hold", 0).get_json()["run"]["run_version"], 1)
        self.assertEqual(self.view(a)["run"]["state"], "held")
        op_after = self.op.get(f"/api/company/companies/{self.cid}").get_json()
        self.assertEqual(op_before["runs"], op_after["runs"])
        self.assertEqual(op_before["history"], op_after["history"])
        self.assertEqual(op_before["package"]["sha256"], op_after["package"]["sha256"])

    def test_same_request_id_replays_and_different_body_conflicts_per_scope(self):
        a, ca = self.visitor()
        b, cb = self.visitor()
        tid = self.task_id(a)
        first = self.run_action(a, ca, tid, "request_inspection", 0, "same-key-000001")
        again = self.run_action(a, ca, tid, "request_inspection", 0, "same-key-000001")
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.get_json()["replayed"])
        self.assertEqual(again.get_json()["run"], first.get_json()["run"])
        self.assertEqual(len(self.view(a)["history"]), 1)  # no second write
        conflict = self.run_action(a, ca, tid, "hold", 0, "same-key-000001")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["error"]["code"], "idempotency_conflict")
        # B using A's key is a fresh request in B's scope, not A's receipt
        other = self.run_action(b, cb, tid, "hold", 0, "same-key-000001")
        self.assertEqual(other.status_code, 200)
        self.assertNotIn("replayed", other.get_json())

    def test_client_cannot_name_another_scope_or_session(self):
        a, ca = self.visitor()
        b, cb = self.visitor()
        tid = self.task_id(a)
        self.run_action(a, ca, tid, "request_inspection", 0)
        r = self.vpost(b, cb, f"/api/demo/companies/{self.cid}/run", {
            "task_id": tid, "action": "hold", "expected_run_version": 1,
            "demo_session_ref": "x", "request_scope": "demo:other", "session_id": "a"})
        self.assertEqual(r.status_code, 409)  # B is still at version 0; extra fields are ignored
        self.assertEqual(self.view(a)["run"]["state"], "inspected")

    def test_release_requires_inspection_and_public_view_is_synthetic_only(self):
        a, ca = self.visitor()
        tid = self.task_id(a)
        self.assertEqual(self.run_action(a, ca, tid, "release", 0).status_code, 409)
        self.assertEqual(self.view(a)["run"]["run_version"], 0)
        item = a.get(f"/api/demo/companies/{self.cid}").get_json()["item"]
        self.assertNotIn("answers", item)
        self.assertNotIn("documents", item)
        listing = a.get("/api/demo/works").get_json()
        self.assertTrue(all(i["synthetic"] for i in listing["items"]))
        self.assertEqual(a.get("/api/demo/rights").status_code, 404)

    def test_revoked_approval_holds_public_run(self):
        a, ca = self.visitor()
        tid = self.task_id(a)
        r = self.opost("/api/company/approvals/revoke", {
            "subject_type": "works", "subject_id": "wrk_seed01", "subject_version": 1,
            "reason": "검토 오류", "evidence": "검토 기록", "actor": "지정 운영책임자"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.run_action(a, ca, tid, "request_inspection", 0).status_code, 409)
        self.assertTrue(a.get(f"/api/demo/companies/{self.cid}").get_json()["item"]["hold"])


class SessionAndCsrfTest(PublicBase):
    def test_forged_visitor_cookie_is_refused_for_mutation(self):
        client = self.app.test_client()
        client.set_cookie("fm_demo", "forged.cookie.value", domain="localhost")
        r = client.post(f"/api/demo/companies/{self.cid}/reset",
                        json={"task_id": "t", "expected_run_version": 0}, headers={"X-CSRF-Token": "x"})
        self.assertEqual(r.status_code, 401)
        # an operator cookie is not a visitor cookie (separate purpose/salt)
        op_cookie = self.op.get_cookie("fm_op").value
        client.set_cookie("fm_demo", op_cookie, domain="localhost")
        r = client.post(f"/api/demo/companies/{self.cid}/reset",
                        json={"task_id": "t", "expected_run_version": 0}, headers={"X-CSRF-Token": "x"})
        self.assertEqual(r.status_code, 401)

    def test_expired_visitor_session_refused(self):
        self.clock.at = END - timedelta(days=10)
        client, csrf = self.visitor()
        tid = self.task_id(client)
        self.clock.at += timedelta(hours=7)  # visitor ttl is 6h
        r = self.vpost(client, csrf, f"/api/demo/companies/{self.cid}/reset",
                       {"task_id": tid, "expected_run_version": 0})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.get_json()["error"]["code"], "session_invalid")

    def test_missing_and_wrong_csrf_and_cross_origin_refused(self):
        client, csrf = self.visitor()
        tid = self.task_id(client)
        path = f"/api/demo/companies/{self.cid}/reset"
        body = {"task_id": tid, "expected_run_version": 0}
        self.assertEqual(client.post(path, json=body).status_code, 403)
        self.assertEqual(client.post(path, json=body, headers={"X-CSRF-Token": "wrong"}).status_code, 403)
        other = self.app.test_client()
        _, other_csrf = self.visitor()  # a token from another session
        self.assertEqual(client.post(path, json=body, headers={"X-CSRF-Token": other_csrf}).status_code, 403)
        evil = client.post(path, json=body, headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"})
        self.assertEqual(evil.status_code, 403)
        self.assertEqual(client.post(path, json=body, headers={"X-CSRF-Token": csrf}).status_code, 200)

    def test_operator_expired_session_and_bad_token(self):
        self.assertEqual(self.app.test_client().post("/api/auth/login", json={"token": "nope"}).status_code, 401)
        self.assertEqual(self.app.test_client().post("/api/auth/login", json={}).status_code, 401)
        self.clock.at += timedelta(hours=9)  # operator ttl is 8h
        self.assertEqual(self.op.get("/api/company/companies").status_code, 401)

    def test_operator_mutation_needs_csrf(self):
        body = {"subject_type": "works", "subject_id": "wrk_seed01", "purposes": ["training"],
                "evidence": "e", "confirmed_by": "c"}
        self.assertEqual(self.op.post("/api/company/rights", json=body).status_code, 403)
        self.assertEqual(self.op.post("/api/company/rights", json=body,
                                      headers={"X-CSRF-Token": "bad"}).status_code, 403)
        self.assertEqual(self.opost("/api/company/rights", body).status_code, 201)

    def test_cookies_are_httponly_samesite_and_responses_no_store(self):
        r = self.app.test_client().post("/api/auth/login", json={"token": OP_TOKEN})
        cookie = r.headers.get("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        for path in ("/", "/static/styles.css", "/api/demo/bootstrap", "/api/records/materials"):
            self.assertEqual(self.op.get(path).headers.get("Cache-Control"), "no-store", path)
        self.assertEqual(self.app.test_client().get("/api/health").headers.get("Cache-Control"), "no-store")


class OperatorOnlyTest(PublicBase):
    def test_anonymous_direct_api_refused_including_gets(self):
        anon = self.app.test_client()
        gets = ["/api/health", "/api/records/materials", "/api/company/companies", "/api/company/candidates",
                f"/api/company/companies/{self.cid}", f"/api/company/companies/{self.cid}/export",
                f"/api/company/companies/{self.cid}/runs", "/api/rights", "/api/nonexistent"]
        for path in gets:
            self.assertEqual(anon.get(path).status_code, 401, path)
        posts = ["/api/company/companies", "/api/company/rights", "/api/company/approvals/revoke",
                 "/api/records/materials"]
        for path in posts:
            self.assertEqual(anon.post(path, json={}).status_code, 401, path)
        self.assertEqual(anon.patch("/api/records/materials/mat_seed01", json={}).status_code, 401)

    def test_visitor_cookie_does_not_open_operator_routes(self):
        client, csrf = self.visitor()
        self.assertEqual(client.get("/api/company/companies").status_code, 401)
        r = client.post("/api/company/companies", json={}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(client.get(f"/api/company/companies/{self.cid}/export").status_code, 401)

    def test_operator_still_works_and_demo_has_no_operator_actions(self):
        self.assertEqual(self.op.get("/api/company/companies").status_code, 200)
        self.assertEqual(self.op.get(f"/api/company/companies/{self.cid}/export").status_code, 200)
        client, csrf = self.visitor()
        for path in ("/api/demo/companies", "/api/demo/rights", "/api/demo/export"):
            r = client.post(path, json={}, headers={"X-CSRF-Token": csrf})
            self.assertIn(r.status_code, (404, 405), path)

    def test_missing_settings_fail_closed(self):
        for drop in ("FM_SECRET_KEY", "OPERATOR_TOKEN", "FM_PUBLIC_END"):
            env = {k: v for k, v in ENV.items() if k != drop}
            app = create_app(ROOT, Path(self.tmp.name) / f"{drop}.sqlite3", env=env, clock=self.clock)
            self.assertEqual(app.test_client().get("/").status_code, 503, drop)
        short = dict(ENV, FM_SECRET_KEY="short")
        app = create_app(ROOT, Path(self.tmp.name) / "short.sqlite3", env=short, clock=self.clock)
        self.assertEqual(app.test_client().get("/api/demo/bootstrap").status_code, 503)

    def test_non_public_mode_is_unchanged(self):
        app = create_app(ROOT, Path(self.tmp.name) / "local.sqlite3", env={})
        client = app.test_client()
        self.assertEqual(client.get("/api/company/companies").status_code, 200)
        self.assertEqual(client.get("/api/demo/bootstrap").status_code, 404)


if __name__ == "__main__":
    unittest.main()


class OperatorEndTest(unittest.TestCase):
    """FM_OPERATOR_END: after it, operator login and every route are refused too."""

    def test_operator_hard_stop(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        op_end = datetime(2026, 10, 24, 22, 0, 0, tzinfo=KST)
        clock = Clock(op_end - timedelta(seconds=1))
        app = create_app(ROOT, Path(tmp.name) / "pub.sqlite3",
                         env={**ENV, "FM_OPERATOR_END": "2026-10-24T13:00:00Z"}, clock=clock)
        client = app.test_client()
        self.assertEqual(client.post("/api/auth/login", json={"token": OP_TOKEN}).status_code, 200)
        self.assertEqual(client.get("/api/records/works").status_code, 200)
        clock.at = op_end
        self.assertEqual(client.get("/api/records/works").status_code, 410)
        self.assertEqual(client.post("/api/auth/login", json={"token": OP_TOKEN}).status_code, 410)
        self.assertEqual(client.get("/").status_code, 410)
