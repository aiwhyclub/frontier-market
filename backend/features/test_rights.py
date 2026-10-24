import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from backend import db
from backend.app import create_app
from backend.features import rights

ROOT = Path(__file__).resolve().parent.parent.parent


class RightsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "records.sqlite3"
        self.app = create_app(ROOT, self.db_path)
        if "rights" not in self.app.blueprints:
            self.app.register_blueprint(rights.bp)
        with closing(db.connect(self.db_path)) as conn:
            rights.init_schema(conn)
        self.client = self.app.test_client()
        r = self.client.post("/api/records/materials", json={
            "title": "권리 테스트 자료", "body": "합성 본문 내용입니다.", "synthetic": True})
        self.mid = r.get_json()["item"]["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def _grant(self, **over):
        payload = {
            "subject_type": "materials", "subject_id": self.mid, "subject_version": 1,
            "holder": "가상 권리자", "purposes": ["training", "evaluation"],
            "derivative": "allowed", "distribution": "denied", "reason": "합성 연습 허용 확인",
            "evidence": "합성 동의 예시 문서", "actor": "운영책임자A", "status": "confirmed",
        }
        payload.update(over)
        return self.client.post("/api/rights", json=payload)

    def _state(self, purpose=None):
        q = f"?subject_type=materials&subject_id={self.mid}"
        if purpose:
            q += f"&purpose={purpose}"
        return self.client.get("/api/rights/state" + q).get_json()

    def _set_material_status(self, status):
        with closing(db.connect(self.db_path)) as conn:
            with db.write_transaction(conn):
                cur = db.get_record(conn, "materials", self.mid)
                db.update_record(conn, "materials", self.mid, expected_version=cur["version"],
                                 changes={"status": status})

    def test_confirm_binds_revision_hash_and_allows_purposes(self):
        r = self._grant()
        self.assertEqual(r.status_code, 201, r.get_json())
        body = r.get_json()
        self.assertEqual(body["item"]["effective"], "confirmed")
        with closing(db.connect(self.db_path)) as conn:
            rev = conn.execute("SELECT material_hash FROM record_revisions WHERE type='materials' "
                               "AND id=? AND version=1", (self.mid,)).fetchone()
        self.assertEqual(body["item"]["subject_material_hash"], rev["material_hash"])
        self.assertEqual(self._state("training")["state"], "allowed")
        self.assertEqual(self._state("evaluation")["state"], "allowed")

    def test_purpose_not_covered_is_hold(self):
        self._grant(purposes=["evaluation"])
        state = self._state("training")
        self.assertEqual(state["state"], "hold")
        self.assertTrue(any("training" in x for x in state["reasons"]))

    def test_status_only_version_bump_keeps_grant(self):
        self._grant()
        self._set_material_status("reviewed")
        state = self._state("training")
        self.assertEqual(state["state"], "allowed")
        self.assertEqual(state["subject"]["version"], 2)

    def test_content_change_needs_new_confirmation(self):
        self._grant()
        with closing(db.connect(self.db_path)) as conn:
            with db.write_transaction(conn):
                cur = db.get_record(conn, "materials", self.mid)
                db.update_record(conn, "materials", self.mid, expected_version=cur["version"],
                                 changes={"body": "내용이 바뀐 본문입니다."})
        state = self._state("training")
        self.assertEqual(state["state"], "hold")
        self.assertEqual(state["past_grant_count"], 1)

    def test_revoke_holds_and_records_event(self):
        rid = self._grant().get_json()["item"]["id"]
        r = self.client.patch(f"/api/rights/{rid}", json={
            "expected_version": 1, "status": "revoked", "reason": "권리자 철회 통보",
            "actor": "운영책임자A", "evidence": "철회 메일 기록", "purposes": ["training"],
            "derivative": "allowed", "distribution": "denied", "holder": "가상 권리자"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self._state("training")["state"], "hold")
        hist = self.client.get(f"/api/rights/history/materials/{self.mid}").get_json()
        actions = [e["action"] for e in hist["events"]]
        self.assertIn("create", actions)
        self.assertIn("status_change", actions)

    def test_stale_expected_version_is_409(self):
        rid = self._grant().get_json()["item"]["id"]
        r = self.client.patch(f"/api/rights/{rid}", json={
            "expected_version": 99, "status": "unconfirmed", "reason": "x", "actor": "a",
            "purposes": ["training"], "derivative": "unknown", "distribution": "unknown",
            "holder": "h"})
        self.assertEqual(r.status_code, 409)

    def test_no_grant_is_hold(self):
        self.assertEqual(self._state()["state"], "hold")

    def test_validation_errors(self):
        self.assertEqual(self._grant(reason="").status_code, 400)
        self.assertEqual(self._grant(purposes=[]).status_code, 400)
        self.assertEqual(self._grant(evidence="").status_code, 400)
        self.assertEqual(self._grant(status="restricted", restrictions="").status_code, 400)
        self.assertEqual(self._grant(subject_version=50).status_code, 400)
        self.assertEqual(self._grant(subject_id="none_x").status_code, 404)
        self.assertEqual(self._grant(derivative="maybe").status_code, 400)

    def test_expired_grant_is_hold(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.assertEqual(self._grant(valid_until=yesterday).status_code, 400)  # rejected at entry
        rid = self._grant(valid_until=(date.today() + timedelta(days=30)).isoformat()).get_json()["item"]["id"]
        self.assertEqual(self._state("training")["state"], "allowed")
        with closing(db.connect(self.db_path)) as conn:
            with db.write_transaction(conn):
                row = db.get_record(conn, "rights", rid)
                fields = {k: row[k] for k in ("subject_type", "subject_id", "subject_version",
                                              "subject_material_hash", "holder", "purposes",
                                              "derivative", "distribution", "reason", "evidence",
                                              "confirmed_by", "confirmed_at")}
                fields["valid_until"] = yesterday  # simulate the period ending after entry
                db.update_record(conn, "rights", rid, expected_version=row["version"],
                                 changes={"fields": fields})
        state = self._state("training")
        self.assertEqual(state["state"], "hold")
        self.assertTrue(any("끝났습니다" in x for x in state["reasons"]))

    def test_history_lists_revisions_and_events(self):
        rid = self._grant().get_json()["item"]["id"]
        hist = self.client.get(f"/api/rights/history/materials/{self.mid}").get_json()
        self.assertEqual(hist["current"]["id"], self.mid)
        self.assertTrue(hist["revisions"][0]["is_current"])
        self.assertEqual(hist["rights"][0]["id"], rid)
        self.assertEqual(hist["events"][0]["action"], "create")
        detail = self.client.get(f"/api/rights/{rid}").get_json()
        self.assertEqual(detail["item"]["id"], rid)
        self.assertEqual(len(detail["revisions"]), 1)

    def test_seeded_legacy_rights_do_not_allow_use(self):
        state = self.client.get("/api/rights/state?subject_type=materials&subject_id=mat_seed01").get_json()
        self.assertEqual(state["state"], "hold")

    def test_listing_filters_by_subject(self):
        self._grant()
        data = self.client.get(f"/api/rights?subject_type=materials&subject_id={self.mid}").get_json()
        self.assertEqual(data["total"], 1)
        self.assertTrue(data["items"][0]["applies_now"])
        self.assertTrue(data["items"][0]["synthetic"])


if __name__ == "__main__":
    unittest.main()
