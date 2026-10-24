"""EPIC 11.4 integrity checks: retries, version conflicts, rollback, restart and restore.

All databases live in temporary directories. Synthetic data only.
"""

import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend import db
from backend.app import create_app
from backend.backup import BackupError, backup_db, restore_db

ROOT = Path(__file__).resolve().parent.parent
EXPERT_BODY = {
    "name": "가상 전문가", "field": "데이터", "experience": "합성 경력", "apprenticeship": "합성 도제",
    "synthetic": True,
}
FEEDBACK_BODY = {"kind": "topic", "title": "가상 의견", "note": "합성 메모", "synthetic": True}


class IntegrityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "integrity.sqlite3"
        self.app = create_app(ROOT, self.db_path)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def _count(self, rtype):
        with closing(db.connect(self.db_path)) as conn:
            return db.counts(conn)[rtype]

    # --- duplicate retries -------------------------------------------------------------
    def test_retried_expert_create_with_same_key_saves_once(self):
        before = self._count("experts")
        headers = {"Idempotency-Key": "retry-expert-0001"}
        first = self.client.post("/api/experts", json=EXPERT_BODY, headers=headers)
        second = self.client.post("/api/experts", json=EXPERT_BODY, headers=headers)
        self.assertIn(first.status_code, (200, 201))
        self.assertIn(second.status_code, (200, 201))
        self.assertEqual(first.get_json()["item"]["id"], second.get_json()["item"]["id"])
        self.assertEqual(self._count("experts"), before + 1)

    def test_retried_feedback_create_with_same_key_saves_once(self):
        before = self._count("feedback")
        headers = {"Idempotency-Key": "retry-feedback-0001"}
        self.client.post("/api/feedback", json=FEEDBACK_BODY, headers=headers)
        self.client.post("/api/feedback", json=FEEDBACK_BODY, headers=headers)
        self.assertEqual(self._count("feedback"), before + 1)

    def test_same_key_with_different_body_is_refused_not_merged(self):
        headers = {"Idempotency-Key": "reuse-key-0001"}
        self.client.post("/api/experts", json=EXPERT_BODY, headers=headers)
        other = dict(EXPERT_BODY, name="다른 전문가")
        res = self.client.post("/api/experts", json=other, headers=headers)
        self.assertEqual(res.status_code, 409)
        self.assertNotIn("Traceback", res.get_data(as_text=True))

    # --- version conflicts -------------------------------------------------------------
    def test_stale_expert_patch_returns_safe_409(self):
        created = self.client.post("/api/experts", json=EXPERT_BODY).get_json()["item"]
        stale = created["version"] + 5
        res = self.client.patch(f"/api/experts/{created['id']}",
                                json={"field": "새 분야", "expected_version": stale})
        self.assertEqual(res.status_code, 409)
        message = res.get_json()["error"]["message"]
        self.assertNotRegex(message, r"version \d+ != expected|Traceback|sqlite")

    def test_stale_material_patch_returns_safe_409(self):
        res = self.client.post("/api/collect/materials",
                               json={"title": "가상 자료", "body": "합성 본문", "synthetic": True})
        self.assertIn(res.status_code, (200, 201))
        mid = res.get_json()["item"]["id"]
        patch = self.client.patch(f"/api/collect/materials/{mid}",
                                  json={"title": "바꾼 제목", "expected_version": 999})
        self.assertEqual(patch.status_code, 409)
        self.assertNotRegex(patch.get_json()["error"]["message"], r"version \d+ != expected|sqlite")

    # --- failed write leaves nothing behind -----------------------------------------
    def test_exception_inside_write_transaction_leaves_no_partial_rows(self):
        with closing(db.connect(self.db_path)) as conn:
            before_rev = conn.execute("SELECT COUNT(*) FROM record_revisions").fetchone()[0]
            with self.assertRaises(RuntimeError):
                with db.write_transaction(conn):
                    db.create_record(conn, "materials", title="실패 자료", status="draft",
                                     synthetic=True, body="본문")
                    raise RuntimeError("simulated failure")
            after_rev = conn.execute("SELECT COUNT(*) FROM record_revisions").fetchone()[0]
            self.assertEqual(after_rev, before_rev)
            self.assertFalse(conn.in_transaction)
            titles = [r[0] for r in conn.execute("SELECT title FROM records WHERE title = '실패 자료'")]
            self.assertEqual(titles, [])

    # --- restart keeps data and does not reseed -----------------------------------
    def test_restart_does_not_duplicate_seed_or_lose_writes(self):
        self.client.post("/api/experts", json=EXPERT_BODY)
        before = db.counts(db.connect(self.db_path))
        restarted = create_app(ROOT, self.db_path).test_client()
        after = restarted.get("/api/records/summary").get_json()["counts"]
        self.assertEqual(after, before)

    # --- backup / restore consistency ---------------------------------------------
    def test_restore_after_later_writes_matches_backup_exactly(self):
        backup_path = Path(self.tmp.name) / "snap.sqlite3"
        backup_db(self.db_path, backup_path)
        snapshot = db.counts(db.connect(backup_path))
        self.client.post("/api/experts", json=EXPERT_BODY)
        restore_db(backup_path, self.db_path)
        restored = create_app(ROOT, self.db_path).test_client()
        self.assertEqual(restored.get("/api/records/summary").get_json()["counts"], snapshot)

    def test_corrupt_backup_does_not_touch_live_db(self):
        bad = Path(self.tmp.name) / "bad.sqlite3"
        bad.write_bytes(b"not a sqlite file at all" * 100)
        before = self._count("experts")
        with self.assertRaises(BackupError):
            restore_db(bad, self.db_path)
        self.assertEqual(self._count("experts"), before)


if __name__ == "__main__":
    unittest.main()
