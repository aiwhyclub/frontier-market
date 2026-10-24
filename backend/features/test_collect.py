import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from backend import db
from backend.app import create_app
from backend.features import collect

ROOT = Path(__file__).resolve().parent.parent.parent


class FakeResp:
    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self._headers = headers or {}
        self._body = body
        self.closed = False

    def getheaders(self):
        return list(self._headers.items())

    def read(self, amount=-1):
        return self._body[:amount] if amount >= 0 else self._body

    def close(self):
        self.closed = True


def page(html, status=200, ctype="text/html; charset=utf-8"):
    return (status, {"content-type": ctype}, FakeResp(status, {"content-type": ctype}, html.encode("utf-8")))


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(ROOT, Path(self.tmp.name) / "test.sqlite3")
        self.client = self.app.test_client()
        self.db_path = self.app.config["DB_PATH"]
        self.patches = [
            mock.patch.object(collect, "_resolve", side_effect=lambda host, port: ["93.184.216.34"]),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def count_materials(self):
        with closing(db.connect(self.db_path)) as conn:
            return db.counts(conn)["materials"]

    def create_manual(self, title="실습 기록", body="첫 문장입니다. 둘째 문장입니다. 셋째 문장입니다."):
        res = self.client.post("/api/collect/materials", json={"title": title, "body": body})
        self.assertEqual(res.status_code, 201, res.get_json())
        return res.get_json()["item"]

    # F1.1 URL fetch and SSRF rules

    def test_blocked_addresses_are_rejected_without_dns(self):
        for url in ["http://127.0.0.1/a", "http://169.254.169.254/latest", "http://10.0.0.5/",
                    "http://[::1]/", "http://localhost/x", "http://192.168.0.1/"]:
            with self.assertRaises(collect.FetchError, msg=url) as ctx:
                collect._check_public_url(url)
            self.assertIn(ctx.exception.code, ("blocked_address",), url)

    def test_scheme_and_port_rules(self):
        with self.assertRaises(collect.FetchError) as ctx:
            collect._check_public_url("ftp://example.com/")
        self.assertEqual(ctx.exception.code, "unsupported_scheme")
        with self.assertRaises(collect.FetchError) as ctx:
            collect._check_public_url("http://example.com:8080/")
        self.assertEqual(ctx.exception.code, "blocked_port")

    def test_private_resolution_is_blocked(self):
        with mock.patch.object(collect, "_resolve", side_effect=lambda h, p: ["10.1.2.3"]):
            with self.assertRaises(collect.FetchError) as ctx:
                collect._check_public_url("https://example.com/")
        self.assertEqual(ctx.exception.code, "blocked_address")

    def test_fetch_success_keeps_original_and_final_url(self):
        html = "<html><head><title>실습 자료</title><script>var x=1;</script></head><body><p>본문 내용입니다.</p></body></html>"
        with mock.patch.object(collect, "_fetch_once", return_value=page(html)):
            res = self.client.post("/api/collect/fetch", json={"url": "https://example.com/a"})
        self.assertEqual(res.status_code, 201, res.get_json())
        item = res.get_json()["item"]
        self.assertEqual(item["title"], "실습 자료")
        self.assertIn("본문 내용입니다.", item["body"])
        self.assertNotIn("var x", item["body"])
        self.assertEqual(item["origin"], "url")
        self.assertEqual(item["source_url"], "https://example.com/a")
        self.assertEqual(item["final_url"], "https://example.com/a")
        self.assertTrue(item["fetched_at"])
        self.assertEqual(len(item["content_hash"]), 64)
        self.assertTrue(item["synthetic"])

    def test_redirect_is_rechecked_and_limited(self):
        redirect = (302, {"location": "http://127.0.0.1/admin"}, FakeResp(302))
        with mock.patch.object(collect, "_fetch_once", return_value=redirect):
            res = self.client.post("/api/collect/fetch", json={"url": "https://example.com/r"})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.get_json()["error"]["reason"], "blocked_address")
        loop = (302, {"location": "/again"}, FakeResp(302))
        with mock.patch.object(collect, "_fetch_once", return_value=loop):
            res = self.client.post("/api/collect/fetch", json={"url": "https://example.com/loop"})
        self.assertEqual(res.get_json()["error"]["reason"], "too_many_redirects")

    def test_failure_returns_reason_keeps_input_and_creates_nothing(self):
        before = self.count_materials()
        with mock.patch.object(collect, "_fetch_once", return_value=page("x", status=404)):
            res = self.client.post("/api/collect/fetch", json={"url": "https://example.com/missing"})
        self.assertEqual(res.status_code, 422)
        err = res.get_json()["error"]
        self.assertEqual(err["reason"], "http_error")
        self.assertEqual(err["input"]["url"], "https://example.com/missing")
        self.assertEqual(self.count_materials(), before)

    def test_unsupported_type_and_too_large(self):
        pdf = (200, {"content-type": "application/pdf"}, FakeResp(200, {}, b"%PDF"))
        with mock.patch.object(collect, "_fetch_once", return_value=pdf):
            res = self.client.post("/api/collect/fetch", json={"url": "https://example.com/a.pdf"})
        self.assertEqual(res.get_json()["error"]["reason"], "unsupported_type")
        big = b"a" * (collect.MAX_BYTES + 10)
        huge = (200, {"content-type": "text/plain"}, FakeResp(200, {"content-type": "text/plain"}, big))
        with mock.patch.object(collect, "_fetch_once", return_value=huge):
            res = self.client.post("/api/collect/fetch", json={"url": "https://example.com/big"})
        self.assertEqual(res.get_json()["error"]["reason"], "too_large")

    def test_timeout_is_reported(self):
        with mock.patch.object(collect, "_fetch_once", side_effect=TimeoutError()):
            res = self.client.post("/api/collect/fetch", json={"url": "https://example.com/slow"})
        self.assertEqual(res.get_json()["error"]["reason"], "timeout")

    # F1.2 direct body

    def test_direct_body_is_stored_and_readable(self):
        item = self.create_manual()
        res = self.client.get(f"/api/collect/materials/{item['id']}")
        detail = res.get_json()
        self.assertEqual(detail["item"]["body"], "첫 문장입니다. 둘째 문장입니다. 셋째 문장입니다.")
        self.assertEqual(detail["item"]["origin"], "manual")
        listed = self.client.get("/api/records/materials").get_json()["items"]
        self.assertTrue(any(i["id"] == item["id"] for i in listed))

    def test_direct_body_rejects_non_synthetic_and_missing_body(self):
        res = self.client.post("/api/collect/materials", json={"title": "t", "body": "b", "synthetic": False})
        self.assertEqual(res.status_code, 400)
        self.assertIn("synthetic", res.get_json()["error"]["fields"])
        res = self.client.post("/api/collect/materials", json={"title": "t", "body": "  "})
        self.assertIn("body", res.get_json()["error"]["fields"])

    # F1.3 metadata and revisions

    def test_metadata_edit_round_trip(self):
        item = self.create_manual()
        res = self.client.patch(f"/api/collect/materials/{item['id']}", json={
            "expected_version": item["version"], "title": "수정된 제목", "kind": "회계", "source_type": "메모"})
        self.assertEqual(res.status_code, 200, res.get_json())
        got = self.client.get(f"/api/collect/materials/{item['id']}").get_json()["item"]
        self.assertEqual(got["title"], "수정된 제목")
        self.assertEqual(got["kind"], "회계")
        self.assertEqual(got["source_type"], "메모")

    def test_body_revision_needs_reason_owner_and_marks_dependent_work(self):
        item = self.create_manual()
        with closing(db.connect(self.db_path)) as conn:
            with db.write_transaction(conn):
                wid = db.create_record(conn, "works", title="작업", status="draft", synthetic=True,
                                       body="원문 기반 작업", fields={"review_state": "approved"})
                db.add_link(conn, "works", wid, "materials", item["id"], "from")
        res = self.client.patch(f"/api/collect/materials/{item['id']}", json={
            "expected_version": item["version"], "body": "개정된 원문입니다."})
        self.assertEqual(res.status_code, 400)
        self.assertIn("reason", res.get_json()["error"]["fields"])
        res = self.client.patch(f"/api/collect/materials/{item['id']}", json={
            "expected_version": item["version"], "body": "개정된 원문입니다.",
            "reason": "오탈자 수정", "owner": "운영자"})
        self.assertEqual(res.status_code, 200, res.get_json())
        body = res.get_json()
        self.assertTrue(body["changed"])
        self.assertEqual(body["dependent_works"], [wid])
        detail = self.client.get(f"/api/collect/materials/{item['id']}").get_json()
        self.assertEqual(detail["item"]["body"], "개정된 원문입니다.")
        self.assertEqual(detail["events"][0]["kind"], "revision")
        self.assertEqual(detail["events"][0]["reason"], "오탈자 수정")
        self.assertEqual(len(detail["revisions"]), 2)
        old = self.client.get(f"/api/collect/materials/{item['id']}/revisions/1").get_json()
        self.assertEqual(old["snapshot"]["body"], "첫 문장입니다. 둘째 문장입니다. 셋째 문장입니다.")
        work = self.client.get(f"/api/records/works/{wid}").get_json()["item"]
        self.assertEqual(work["review_state"], "review_required")

    def test_stale_version_conflicts(self):
        item = self.create_manual()
        self.client.patch(f"/api/collect/materials/{item['id']}", json={
            "expected_version": item["version"], "title": "새 제목"})
        res = self.client.patch(f"/api/collect/materials/{item['id']}", json={
            "expected_version": item["version"], "title": "오래된 제목"})
        self.assertEqual(res.status_code, 409)

    # F1.5 draft, adopt, correct

    def test_draft_is_separate_from_body_and_adopt_or_correct_is_explicit(self):
        item = self.create_manual()
        res = self.client.post(f"/api/collect/materials/{item['id']}/draft", json={"expected_version": item["version"]})
        self.assertEqual(res.status_code, 200, res.get_json())
        drafted = res.get_json()["item"]
        self.assertEqual(drafted["body"], item["body"])
        self.assertEqual(drafted["draft"]["mode"], "rule_based")
        self.assertIsNone(drafted.get("organized"))

        res = self.client.post(f"/api/collect/materials/{item['id']}/organized", json={
            "expected_version": drafted["version"], "action": "adopt", "reviewer": "운영자"})
        self.assertEqual(res.status_code, 200, res.get_json())
        adopted = res.get_json()["item"]
        self.assertEqual(adopted["organized"]["origin"], "rule_draft_adopted")
        self.assertEqual(adopted["organized"]["organized_version"], 1)
        self.assertEqual(adopted["body"], item["body"])

        res = self.client.post(f"/api/collect/materials/{item['id']}/organized", json={
            "expected_version": adopted["version"], "action": "correct",
            "correction_notes": "용어 수정", "reviewer": "운영자"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("text", res.get_json()["error"]["fields"])
        res = self.client.post(f"/api/collect/materials/{item['id']}/organized", json={
            "expected_version": adopted["version"], "action": "correct", "text": "교정본",
            "correction_notes": "용어 수정", "reviewer": "운영자"})
        self.assertEqual(res.status_code, 200, res.get_json())
        corrected = res.get_json()["item"]["organized"]
        self.assertEqual(corrected["origin"], "rule_draft_corrected")
        self.assertEqual(corrected["organized_version"], 2)
        self.assertEqual(corrected["text"], "교정본")

    def test_adopt_refused_when_body_changed_after_draft(self):
        item = self.create_manual()
        drafted = self.client.post(f"/api/collect/materials/{item['id']}/draft",
                                   json={"expected_version": item["version"]}).get_json()["item"]
        self.client.patch(f"/api/collect/materials/{item['id']}", json={
            "expected_version": drafted["version"], "body": "다른 원문입니다.", "reason": "개정", "owner": "운영자"})
        cur = self.client.get(f"/api/collect/materials/{item['id']}").get_json()["item"]
        res = self.client.post(f"/api/collect/materials/{item['id']}/organized", json={
            "expected_version": cur["version"], "action": "adopt", "reviewer": "운영자"})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()["error"]["code"], "stale_draft")

    def test_adopt_requires_reviewer_and_draft(self):
        item = self.create_manual()
        res = self.client.post(f"/api/collect/materials/{item['id']}/organized", json={
            "expected_version": item["version"], "action": "adopt"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("reviewer", res.get_json()["error"]["fields"])
        res = self.client.post(f"/api/collect/materials/{item['id']}/organized", json={
            "expected_version": item["version"], "action": "adopt", "reviewer": "운영자"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("draft", res.get_json()["error"]["fields"])


    # F10.1 AI draft (ai.attempt is monkeypatched; no network)
    GOOD = json.dumps({"summary": "요약입니다.", "key_points": ["핵심 하나", "핵심 둘"]}, ensure_ascii=False)

    def run_info(self, ok=True, error=None):
        return {"feature": "F10.1", "attempted": True, "provider": "anthropic", "model": "claude-test",
                "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:01Z",
                "input_hash": "sha256:abc", "ok": ok, "usage": {"input_tokens": 5, "output_tokens": 7},
                "error": error, "mode": "ai" if ok else "rule_based"}

    def draft(self, item, text, run):
        with mock.patch("backend.ai.attempt", return_value=(text, run)):
            res = self.client.post(f"/api/collect/materials/{item['id']}/draft",
                                   json={"expected_version": item["version"]})
        self.assertEqual(res.status_code, 200, res.get_json())
        return res.get_json()["item"]

    def test_ai_success_stores_model_hash_usage_and_attempt(self):
        item = self.create_manual()
        out = self.draft(item, self.GOOD, self.run_info())
        d = out["draft"]
        self.assertEqual((d["mode"], d["model"], d["input_hash"]), ("ai", "claude-test", "sha256:abc"))
        self.assertEqual(d["usage"]["output_tokens"], 7)
        self.assertEqual(d["key_points"], ["핵심 하나", "핵심 둘"])
        self.assertEqual(out["body"], item["body"])
        self.assertEqual(len(out["draft_attempts"]), 1)
        self.assertTrue(out["draft_attempts"][0]["ok"])
        self.assertIsNone(out.get("organized"))

    def test_ai_failure_and_unparsable_fall_back_to_rule_and_are_recorded(self):
        item = self.create_manual()
        out = self.draft(item, None, self.run_info(ok=False, error="URLError: boom"))
        self.assertEqual(out["draft"]["mode"], "rule_based")
        self.assertEqual(out["draft"]["ai_failure"]["error"], "URLError: boom")
        out = self.draft(out, "not json at all", self.run_info())
        self.assertEqual(out["draft"]["mode"], "rule_based")
        self.assertEqual(out["draft"]["ai_failure"]["error"], "unparsable_response")
        self.assertEqual([a["ok"] for a in out["draft_attempts"]], [False, False])

    def test_ai_draft_adopt_records_origin_and_not_adopted_makes_no_attempt(self):
        item = self.create_manual()
        out = self.draft(item, self.GOOD, self.run_info())
        res = self.client.post(f"/api/collect/materials/{item['id']}/organized", json={
            "expected_version": out["version"], "action": "adopt", "reviewer": "운영자"})
        self.assertEqual(res.get_json()["item"]["organized"]["origin"], "ai_draft_adopted")
        other = self.create_manual(title="다른")
        out = self.draft(other, None, {"attempted": False, "input_hash": "x"})
        self.assertEqual(out["draft"]["mode"], "rule_based")
        self.assertNotIn("draft_attempts", out)
        detail = self.client.get(f"/api/collect/materials/{other['id']}").get_json()
        self.assertFalse(detail["ai"]["adopted"])


if __name__ == "__main__":
    unittest.main()
