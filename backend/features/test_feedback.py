import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

from backend import db
from backend.app import create_app
from backend.features import feedback

ROOT = Path(__file__).resolve().parent.parent.parent


class FeedbackApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "records.sqlite3"
        self.app = create_app(ROOT, self.db_path)
        if "feedback" not in self.app.blueprints:
            self.app.register_blueprint(feedback.bp)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def _post(self, **overrides):
        payload = {"kind": "topic", "title": "새 주제", "note": "필요한 판단 과제입니다."}
        payload.update(overrides)
        return self.client.post("/api/feedback", json=payload)

    def _first_id(self, rtype):
        return self.client.get(f"/api/records/{rtype}").get_json()["items"][0]["id"]

    def test_create_each_kind_and_list_by_kind(self):
        for kind in feedback.KIND_LABELS:
            res = self._post(kind=kind, title=f"{kind} 의견")
            self.assertEqual(res.status_code, 201, res.get_json())
            self.assertIs(res.get_json()["item"]["synthetic"], True)
        data = self.client.get("/api/feedback?kind=pilot").get_json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["kind"], "pilot")

    def test_topic_saves_without_any_catalog_on_empty_db(self):
        empty_app = Flask(__name__)
        empty_path = Path(self.tmp.name) / "empty.sqlite3"
        db.init_db(empty_path)
        empty_app.config["DB_PATH"] = str(empty_path)
        empty_app.register_blueprint(feedback.bp)
        client = empty_app.test_client()
        res = client.post("/api/feedback", json={"kind": "topic", "title": "첫 주제", "note": "내용"})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.get_json()["links"], [])
        self.assertEqual(client.get("/api/feedback").get_json()["total"], 1)

    def test_validation_errors_are_field_level(self):
        res = self._post(kind="unknown", title="", note="x")
        self.assertEqual(res.status_code, 400)
        fields = res.get_json()["error"]["field_errors"]
        self.assertIn("kind", fields)
        self.assertIn("title", fields)
        res = self._post(extra="nope")
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/feedback", data="{}", content_type="text/plain")
        self.assertEqual(res.status_code, 415)

    def test_edit_status_and_note_then_reread(self):
        fid = self._post().get_json()["item"]["id"]
        detail = self.client.get(f"/api/feedback/{fid}").get_json()
        ver = detail["item"]["version"]
        res = self.client.patch(f"/api/feedback/{fid}", json={
            "expected_version": ver, "status": "샘플 검토 대기", "note": "수정한 의견",
            "target_note": "대상 메모", "kind": "pilot"})
        self.assertEqual(res.status_code, 200, res.get_json())
        again = self.client.get(f"/api/feedback/{fid}").get_json()["item"]
        self.assertEqual(again["status"], "샘플 검토 대기")
        self.assertEqual(again["note"], "수정한 의견")
        self.assertEqual(again["target_note"], "대상 메모")
        self.assertEqual(again["kind"], "pilot")
        self.assertEqual(again["version"], ver + 1)

    def test_stale_version_conflicts_and_keeps_data(self):
        fid = self._post().get_json()["item"]["id"]
        res = self.client.patch(f"/api/feedback/{fid}", json={"expected_version": 99, "status": "x"})
        self.assertEqual(res.status_code, 409)
        self.assertIsNone(self.client.get(f"/api/feedback/{fid}").get_json()["item"]["status"])

    def test_link_to_follow_up_records_and_relink(self):
        fid = self._post().get_json()["item"]["id"]
        mat = self._first_id("materials")
        cat = self._first_id("catalog")
        res = self.client.patch(f"/api/feedback/{fid}", json={
            "expected_version": 1, "links": [{"type": "materials", "id": mat},
                                             {"type": "catalog", "id": cat}]})
        self.assertEqual(res.status_code, 200, res.get_json())
        types = {(l["type"], l["id"]) for l in res.get_json()["links"]}
        self.assertEqual(types, {("materials", mat), ("catalog", cat)})
        ver = res.get_json()["item"]["version"]
        res = self.client.patch(f"/api/feedback/{fid}", json={
            "expected_version": ver, "links": [{"type": "materials", "id": mat}]})
        self.assertEqual(res.status_code, 200)
        links = self.client.get(f"/api/feedback/{fid}").get_json()["links"]
        self.assertEqual([(l["type"], l["id"]) for l in links], [("materials", mat)])

    def test_dangling_or_disallowed_link_rejected(self):
        fid = self._post().get_json()["item"]["id"]
        res = self.client.patch(f"/api/feedback/{fid}", json={
            "expected_version": 1, "links": [{"type": "materials", "id": "missing_1"}]})
        self.assertEqual(res.status_code, 400)
        res = self.client.patch(f"/api/feedback/{fid}", json={
            "expected_version": 1, "links": [{"type": "feedback", "id": fid}]})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.client.get(f"/api/feedback/{fid}").get_json()["links"], [])

    def test_trail_both_directions(self):
        fid = self._post().get_json()["item"]["id"]
        mat = self._first_id("materials")
        self.client.patch(f"/api/feedback/{fid}", json={
            "expected_version": 1, "links": [{"type": "materials", "id": mat}]})
        out = self.client.get(f"/api/feedback/trail/feedback/{fid}").get_json()
        self.assertEqual([n["id"] for n in out["outgoing"]], [mat])
        inc = self.client.get(f"/api/feedback/trail/materials/{mat}").get_json()
        self.assertIn(fid, [n["id"] for n in inc["incoming"]])
        self.assertEqual(self.client.get("/api/feedback/trail/materials/nope").status_code, 404)
        self.assertEqual(self.client.get("/api/feedback/trail/bogus/x").status_code, 404)

    def test_dashboard_shape(self):
        before = self.client.get("/api/feedback/dashboard").get_json()["feedback_kind_counts"]["topic"]
        self._post()
        data = self.client.get("/api/feedback/dashboard").get_json()
        self.assertEqual({s["type"] for s in data["stages"]}, {"materials", "rights", "experts",
                                                               "works", "catalog", "projects",
                                                               "companies", "feedback"})
        self.assertTrue(data["recent_activity"])
        self.assertEqual(data["feedback"][0]["next_action"], "연결 대상(자료·카탈로그·작업·FDE·회사)을 지정하세요.")
        self.assertEqual(data["feedback_kind_counts"]["topic"], before + 1)

    def test_mocked_no_network_module_has_no_http_clients(self):
        src = Path(feedback.__file__).read_text(encoding="utf-8")
        self.assertNotIn("urllib", src)
        self.assertNotIn("requests", src)
        with mock.patch("socket.socket", side_effect=AssertionError("network")):
            self.assertEqual(self._post().status_code, 201)


if __name__ == "__main__":
    unittest.main()
