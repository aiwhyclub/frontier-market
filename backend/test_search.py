"""STORY 11.1 [F2.3]: GET /api/records/<type> 텍스트·분야·상태 검색 계약 검사 (임시 DB 전용).

create_app이 합성 시드를 넣으므로 전체 개수는 시드 기준선을 잡고, 필터 검사는 ZX11 표식 기록으로 한다.
"""

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend import db
from backend.app import create_app

ROOT = Path(__file__).resolve().parent.parent


class RecordSearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "search.sqlite3"
        self.client = create_app(ROOT, self.db_path).test_client()
        self.baseline = {t: self._get(f"/api/records/{t}")["total"] for t in ("materials", "catalog")}
        with closing(db.connect(self.db_path)) as conn:
            with db.write_transaction(conn):
                self.water = db.create_record(conn, "materials", title="ZX11 수도 요금 안내", status="draft",
                                              synthetic=True, body="상수도 검침 절차", fields={"domain": "행정"})
                self.wage = db.create_record(conn, "materials", title="ZX11 임금 체계", status="approved",
                                             synthetic=True, body="ZX11 연봉 표 100%_기준", fields={"domain": "노무"})
                self.plain = db.create_record(conn, "materials", title="ZX11 분야 없는 자료", status="draft",
                                              synthetic=True, body="ZX11 본문 without domain")
                self.catalog = db.create_record(conn, "catalog", title="ZX11 수도 과제", status="draft",
                                                synthetic=True, body="카탈로그 본문", fields={"domain": "행정"})

    def tearDown(self):
        self.tmp.cleanup()

    def _get(self, path):
        res = self.client.get(path)
        self.assertEqual(res.status_code, 200, path)
        return res.get_json()

    def _ids(self, path):
        body = self._get(path)
        return sorted(item["id"] for item in body["items"]), body["total"]

    def test_no_filters_returns_every_row_of_the_type(self):
        ids, total = self._ids("/api/records/materials?limit=100")
        self.assertEqual(total, self.baseline["materials"] + 3)
        self.assertTrue({self.water, self.wage, self.plain} <= set(ids))

    def test_text_search_matches_title_or_body(self):
        self.assertEqual(self._ids("/api/records/materials?q=검침"), ([self.water], 1))
        self.assertEqual(self._ids("/api/records/materials?q=수도")[1], 1)  # catalog 기록은 제외

    def test_text_search_ignores_case_for_ascii(self):
        self.assertEqual(self._ids("/api/records/materials?q=without")[1], 1)
        self.assertEqual(self._ids("/api/records/materials?q=WITHOUT")[1], 1)

    def test_text_search_escapes_like_wildcards(self):
        # % 와 _ 는 와일드카드가 아니라 문자로 찾는다.
        self.assertEqual(self._ids("/api/records/materials?q=ZX11%25")[1], 0)
        self.assertEqual(self._ids("/api/records/materials?q=ZX11_")[1], 0)
        self.assertEqual(self._ids("/api/records/materials?q=100%25_")[1], 1)

    def test_blank_q_is_no_filter(self):
        self.assertEqual(self._ids("/api/records/materials?q=%20%20")[1], self.baseline["materials"] + 3)

    def test_field_filters_on_domain_value_exactly(self):
        self.assertEqual(self._ids("/api/records/materials?q=ZX11&field=노무"), ([self.wage], 1))
        self.assertEqual(self._ids("/api/records/materials?q=ZX11&field=노")[1], 0)

    def test_status_filter_is_exact(self):
        self.assertEqual(self._ids("/api/records/materials?q=ZX11&status=approved"), ([self.wage], 1))
        self.assertEqual(self._ids("/api/records/materials?q=ZX11&status=appr")[1], 0)

    def test_filters_combine_with_and(self):
        self.assertEqual(self._ids("/api/records/materials?q=ZX11&status=draft")[1], 2)
        self.assertEqual(self._ids("/api/records/materials?q=ZX11&status=draft&field=행정"), ([self.water], 1))

    def test_limit_applies_after_filters_and_total_is_filtered_total(self):
        body = self._get("/api/records/materials?q=ZX11&status=draft&limit=1")
        self.assertEqual(body["total"], 2)
        self.assertEqual(len(body["items"]), 1)

    def test_search_is_limited_to_requested_type(self):
        self.assertEqual(self._ids("/api/records/catalog?q=ZX11%20수도"), ([self.catalog], 1))

    def test_result_item_matches_detail_identity(self):
        ids, _ = self._ids("/api/records/materials?q=ZX11&field=행정")
        detail = self._get(f"/api/records/materials/{ids[0]}")["item"]
        self.assertEqual(detail["id"], ids[0])
        self.assertEqual(detail["title"], "ZX11 수도 요금 안내")

    def test_too_long_filter_is_rejected_with_field_error(self):
        for name in ("q", "field", "status"):
            res = self.client.get(f"/api/records/materials?{name}=" + "가" * 101)
            self.assertEqual(res.status_code, 400, name)
            self.assertEqual(res.get_json()["error"]["code"], "validation_error")
            self.assertIn(name, res.get_json()["error"]["fields"])

    def test_unknown_type_still_404(self):
        res = self.client.get("/api/records/nope?q=a")
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
