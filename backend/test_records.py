import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend import db, seed
from backend.app import create_app

ROOT = Path(__file__).resolve().parent.parent
SEED_COUNTS = {}
for _rtype, *_rest in seed.SEED_RECORDS:
    SEED_COUNTS[_rtype] = SEED_COUNTS.get(_rtype, 0) + 1


class RecordsApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "records.sqlite3"
        self.client = create_app(ROOT, self.db_path).test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def _post_material(self, client, **overrides):
        payload = {"title": "가상 등록 자료", "body": "합성 본문입니다.", "synthetic": True}
        payload.update(overrides)
        return client.post("/api/records/materials", json=payload)

    def test_summary_counts_every_type_with_seed(self):
        data = self.client.get("/api/records/summary").get_json()
        self.assertEqual(set(data["counts"]), set(db.RECORD_TYPES))
        for rtype in db.RECORD_TYPES:
            self.assertEqual(data["counts"][rtype], SEED_COUNTS.get(rtype, 0), rtype)

    def test_list_items_have_contract_fields_and_are_synthetic(self):
        data = self.client.get("/api/records/materials").get_json()
        self.assertEqual(data["total"], SEED_COUNTS["materials"])
        for item in data["items"]:
            for key in ("id", "title", "status", "synthetic", "version", "updated_at"):
                self.assertIn(key, item)
            self.assertIs(item["synthetic"], True)

    def test_unknown_type_returns_json_404(self):
        res = self.client.get("/api/records/unknown")
        self.assertEqual(res.status_code, 404)
        self.assertIn("code", res.get_json()["error"])
        self.assertIn("message", res.get_json()["error"])

    def test_missing_record_returns_404(self):
        res = self.client.get("/api/records/materials/no-such-id")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.get_json()["error"]["code"], "not_found")

    def test_detail_exposes_hashes_version_and_body(self):
        data = self.client.get("/api/records/materials/mat_seed01").get_json()
        item = data["item"]
        self.assertEqual(item["id"], "mat_seed01")
        self.assertRegex(item["material_hash"], r"^[0-9a-f]{64}$")
        self.assertIsNone(item["input_hash"])
        self.assertGreaterEqual(item["version"], 1)
        self.assertIn("body", item)

    def test_work_without_catalog_exposes_reason(self):
        data = self.client.get("/api/records/works/wrk_seed02").get_json()
        missing = [m for m in data["missing_links"] if m["relation"] == "for"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["type"], "catalog")
        self.assertTrue(missing[0]["reason"])
        relations = {(l["relation"], l["type"]) for l in data["links"]}
        self.assertIn(("by", "experts"), relations)

    def test_links_show_the_linked_record_title(self):
        data = self.client.get("/api/records/works/wrk_seed01").get_json()
        catalog_links = [l for l in data["links"] if l["relation"] == "for"]
        self.assertEqual(catalog_links[0]["type"], "catalog")
        self.assertEqual(catalog_links[0]["id"], "cat_seed01")
        self.assertTrue(catalog_links[0]["title"])
        self.assertFalse([m for m in data["missing_links"] if m["relation"] == "for"])

    def test_seed_covers_expert_and_rights_states(self):
        experts = self.client.get("/api/records/experts").get_json()["items"]
        self.assertEqual({e["status"] for e in experts}, {"approved", "candidate"})
        rights = self.client.get("/api/records/rights").get_json()["items"]
        self.assertEqual({r["status"] for r in rights},
                         {"learning_allowed", "eval_only", "unconfirmed"})
        feedback = self.client.get("/api/records/feedback/fb_seed01").get_json()
        self.assertTrue(any(m["type"] == "materials|works|catalog|projects|companies"
                            for m in feedback["missing_links"]))

    def test_material_post_returns_201_and_detail_matches(self):
        res = self._post_material(self.client)
        self.assertEqual(res.status_code, 201)
        item = res.get_json()["item"]
        self.assertTrue(item["id"].startswith("mat_"))
        self.assertIs(item["synthetic"], True)
        detail = self.client.get(f"/api/records/materials/{item['id']}").get_json()["item"]
        self.assertEqual(detail["title"], "가상 등록 자료")
        self.assertEqual(detail["body"], "합성 본문입니다.")

    def test_material_post_validation_errors_are_400_with_fields(self):
        res = self._post_material(self.client, synthetic=False, title="")
        self.assertEqual(res.status_code, 400)
        error = res.get_json()["error"]
        self.assertEqual(error["code"], "validation_error")
        self.assertIn("synthetic", error["fields"])
        self.assertIn("title", error["fields"])

    def test_material_post_rejects_unknown_fields_and_missing_keys(self):
        res = self.client.post("/api/records/materials", json={"title": "x", "role": "admin"})
        self.assertEqual(res.status_code, 400)
        fields = res.get_json()["error"]["fields"]
        self.assertIn("role", fields)
        self.assertIn("body", fields)
        self.assertIn("synthetic", fields)

    def test_material_post_requires_json_content_type(self):
        res = self.client.post("/api/records/materials", data="title=x",
                               content_type="application/x-www-form-urlencoded")
        self.assertEqual(res.status_code, 415)

    def test_material_post_rejects_invalid_json(self):
        res = self.client.post("/api/records/materials", data="{not json",
                               content_type="application/json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"]["code"], "invalid_json")

    def test_material_post_rejects_duplicate_json_keys(self):
        raw = b'{"title":"a","title":"b","body":"c","synthetic":true}'
        res = self.client.post("/api/records/materials", data=raw,
                               content_type="application/json")
        self.assertEqual(res.status_code, 400)


class RestartPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "restart.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_restart_restores_same_id_and_content_without_duplicating_seed(self):
        first = create_app(ROOT, self.db_path).test_client()
        res = first.post("/api/records/materials",
                         json={"title": "재시작 확인", "body": "같은 내용이어야 합니다.", "synthetic": True})
        self.assertEqual(res.status_code, 201)
        created = res.get_json()["item"]
        before = first.get("/api/records/summary").get_json()["counts"]

        second = create_app(ROOT, self.db_path).test_client()
        after = second.get("/api/records/summary").get_json()["counts"]
        self.assertEqual(before, after)
        self.assertEqual(after["materials"], SEED_COUNTS["materials"] + 1)

        restored = second.get(f"/api/records/materials/{created['id']}").get_json()["item"]
        self.assertEqual(restored["id"], created["id"])
        self.assertEqual(restored["title"], created["title"])
        self.assertEqual(restored["body"], created["body"])
        self.assertEqual(restored["material_hash"], created["material_hash"])
        self.assertEqual(restored["version"], created["version"])

    def test_repeated_restarts_do_not_duplicate_seed_rows(self):
        for _ in range(3):
            create_app(ROOT, self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            counts = db.counts(conn)
            marker = db.get_metadata(conn, seed.SEED_MARKER_KEY)
        self.assertEqual(marker, str(seed.SEED_VERSION))
        for rtype, n in SEED_COUNTS.items():
            self.assertEqual(counts[rtype], n, rtype)

    def test_seed_does_not_overwrite_user_edits(self):
        create_app(ROOT, self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            with db.write_transaction(conn):
                current = db.get_record(conn, "materials", "mat_seed01")
                db.update_record(conn, "materials", "mat_seed01",
                                 expected_version=current["version"],
                                 changes={"title": "사용자가 고친 제목"})
        self.assertFalse(seed.ensure_seed(self.db_path))
        app = create_app(ROOT, self.db_path).test_client()
        self.assertEqual(
            app.get("/api/records/materials/mat_seed01").get_json()["item"]["title"],
            "사용자가 고친 제목",
        )


class MigrationAndHashTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "service.sqlite3"
        db.init_db(self.db_path)
        seed.ensure_seed(self.db_path)
        self.conn = db.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_init_is_repeatable_and_records_schema_version(self):
        db.init_db(self.db_path)
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            self.assertEqual(db.get_metadata(conn, "schema_version"), str(db.CURRENT_SCHEMA_VERSION))

    def test_failed_write_rolls_back(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with db.write_transaction(self.conn):
                db.create_record(self.conn, "materials", title="임시", status="draft",
                                 synthetic=True, body="x", record_id="mat_seed01")
        self.assertEqual(db.list_records(self.conn, "materials")[1], SEED_COUNTS["materials"])

    def test_status_change_bumps_version_but_keeps_material_hash(self):
        with db.write_transaction(self.conn):
            before = db.get_record(self.conn, "materials", "mat_seed01")
            after = db.update_record(self.conn, "materials", "mat_seed01",
                                     expected_version=before["version"],
                                     changes={"status": "reviewed"})
        self.assertEqual(after["version"], before["version"] + 1)
        self.assertEqual(after["material_hash"], before["material_hash"])
        self.assertEqual(after["input_hash"], before["input_hash"])

    def test_body_change_changes_material_hash_and_input_hash_is_separate(self):
        with db.write_transaction(self.conn):
            before = db.get_record(self.conn, "materials", "mat_seed01")
            after = db.update_record(self.conn, "materials", "mat_seed01",
                                     expected_version=before["version"],
                                     changes={"body": "실내용이 바뀐 합성 본문"})
        self.assertNotEqual(after["material_hash"], before["material_hash"])
        self.assertEqual(after["version"], before["version"] + 1)

        with db.write_transaction(self.conn):
            reviewed = db.update_record(self.conn, "materials", "mat_seed01",
                                        expected_version=after["version"],
                                        changes={"input_hash": "a" * 64})
        self.assertEqual(reviewed["material_hash"], after["material_hash"])
        self.assertEqual(reviewed["input_hash"], "a" * 64)
        self.assertEqual(reviewed["version"], after["version"] + 1)

    def test_stale_version_is_rejected(self):
        with self.assertRaises(db.RecordConflict):
            with db.write_transaction(self.conn):
                db.update_record(self.conn, "materials", "mat_seed01",
                                 expected_version=999, changes={"status": "x"})

    def test_revisions_keep_every_version(self):
        with db.write_transaction(self.conn):
            before = db.get_record(self.conn, "materials", "mat_seed02")
            db.update_record(self.conn, "materials", "mat_seed02",
                             expected_version=before["version"], changes={"status": "reviewed"})
        rows = self.conn.execute(
            "SELECT version FROM record_revisions WHERE type = 'materials' AND id = 'mat_seed02' "
            "ORDER BY version"
        ).fetchall()
        self.assertEqual([r["version"] for r in rows], [1, 2])

    def test_missing_link_reason_is_listed_by_relations(self):
        links, missing = db.relations(self.conn, "materials", "mat_seed03")
        self.assertEqual([(l["type"], l["relation"]) for l in links], [("works", "from")])
        self.assertTrue(any(m["relation"] == "subject" and m["reason"] for m in missing))

    def test_foreign_key_blocks_link_to_missing_record(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with db.write_transaction(self.conn):
                db.add_link(self.conn, "works", "wrk_seed01", "experts", "no-such", "by")

    def test_seed_contains_only_synthetic_records(self):
        rows = self.conn.execute("SELECT synthetic FROM records").fetchall()
        self.assertTrue(rows)
        self.assertTrue(all(r["synthetic"] == 1 for r in rows))
        bodies = json.dumps([dict(r) for r in self.conn.execute("SELECT * FROM records")], ensure_ascii=False)
        self.assertNotIn("@", bodies)


if __name__ == "__main__":
    unittest.main()
