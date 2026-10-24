"""Tests for S4 catalog cards (EPIC 5, F4.1~F4.5)."""
import json
import os
import tempfile
import unittest
import unittest.mock
from contextlib import closing
from pathlib import Path

from backend import db
from backend.app import create_app
from backend.features import ai_fakes, catalog

ROOT = Path(__file__).resolve().parents[2]


def _card_payload(**overrides):
    payload = {
        "title": "도자 유약 두께 판단",
        "domain": "전통 도예",
        "summary": "가마 앞에서 유약 두께를 눈으로 가늠하는 판단",
        "judgement_task": "이 유약 두께가 소성 후 흐름 불량을 막을지 판단",
        "target_capability": "유약 두께 판정",
        "expected_deliverables": "판정 체크리스트 1장",
    }
    payload.update(overrides)
    return payload


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "catalog.db"
        self.app = create_app(ROOT, self.db_path)
        if "catalog" not in self.app.blueprints:
            self.app.register_blueprint(catalog.bp)
        self.client = self.app.test_client()

    def tearDown(self):
        self._tmp.cleanup()

    def _conn(self):
        return db.connect(self.db_path)

    def _seed(self, expert_status="approved"):
        with closing(self._conn()) as conn, db.write_transaction(conn):
            mid = db.create_record(conn, "materials", title="관찰 노트", status="imported",
                                   synthetic=True, body="합성 관찰 노트")
            eid = db.create_record(conn, "experts", title="가상 숙련자", status=expert_status,
                                   synthetic=True, fields={"field": "전통 도예"})
            wid = db.create_record(conn, "works", title="유약 작업", status="approved",
                                   synthetic=True, body="합성 작업 원문", fields={"expert_id": eid})
            db.add_link(conn, "works", wid, "experts", eid, "by")
            db.add_link(conn, "works", wid, "materials", mid, "from")
            other = db.create_record(conn, "projects", title="다른 프로젝트", status="scoping",
                                     synthetic=True)
        return {"material": mid, "expert": eid, "work": wid, "project": other}

    def _create(self, **overrides):
        resp = self.client.post("/api/catalog/", json=_card_payload(**overrides))
        return resp

    def _patch(self, card_id, version, **data):
        return self.client.patch(f"/api/catalog/{card_id}", json={"expected_version": version, **data})

    def test_create_requires_judgement_fields_and_rejects_bare_tech_name(self):
        resp = self.client.post("/api/catalog/", json={"title": "유약"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("judgement_task", resp.get_json()["error"]["fields"])
        same = self._create(title="유약 두께 판정", target_capability="유약 두께 판정")
        self.assertEqual(same.status_code, 400)
        self.assertIn("title", same.get_json()["error"]["fields"])

    def test_create_stores_card_as_draft_or_hold_with_reasons(self):
        resp = self._create(synthetic=True)
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertTrue(body["item"]["synthetic"])
        self.assertEqual(body["card"]["status"], "hold")
        self.assertTrue(any("원천 자료" in r for r in body["card"]["hold_reasons"]))
        self.assertEqual(len(body["axes"]), 6)
        self.assertTrue(all(a["status"] == "missing" for a in body["axes"]))
        self.assertIn("수요", " ".join(body["card"]["limits"]))

    def test_incomplete_core_is_draft(self):
        resp = self._create()
        card_id = resp.get_json()["item"]["id"]
        patch = self._patch(card_id, 1, summary="   ")
        self.assertEqual(patch.status_code, 400)
        got = self.client.get(f"/api/catalog/{card_id}").get_json()
        self.assertEqual(got["card"]["status"], "hold")

    def test_media_refs_need_http_address_time_and_description(self):
        bad = self._create(media_refs=[{"address": "file:///x", "captured_at": "2026-01-01",
                                        "description": "설명"}])
        self.assertEqual(bad.status_code, 400)
        self.assertIn("media_refs", bad.get_json()["error"]["fields"])
        ok = self._create(media_refs=[{"address": "https://example.com/v", "captured_at": "2026-01-01",
                                       "description": "가마 앞 장면"}])
        self.assertEqual(ok.status_code, 201)
        card = ok.get_json()
        self.assertTrue(any("자동 영상 분석은 수행되지 않았습니다" in l for l in card["card"]["limits"]))

    def test_axes_need_evidence_or_explicit_unknown(self):
        resp = self._create(axes={"origin": "현장 메모 3쪽", "documentation_gap": "미확인"})
        self.assertEqual(resp.status_code, 201)
        axes = {a["key"]: a["status"] for a in resp.get_json()["axes"]}
        self.assertEqual(axes["origin"], "evidence")
        self.assertEqual(axes["documentation_gap"], "unknown")
        self.assertEqual(axes["lab_link"], "missing")
        self.assertEqual(resp.get_json()["card"]["evidence_axis_count"], 1)

    def test_link_rejects_unapproved_expert_wrong_type_and_mismatch(self):
        seeds = self._seed(expert_status="pending")
        card_id = self._create().get_json()["item"]["id"]
        resp = self.client.post(f"/api/catalog/{card_id}/link", json={
            "expected_version": 1, "source_ids": [seeds["material"]],
            "expert_ids": [seeds["expert"]], "work_ids": [seeds["work"]]})
        self.assertEqual(resp.status_code, 422)
        self.assertIn("expert_ids", resp.get_json()["error"]["field_errors"])

        seeds2 = self._seed()
        resp = self.client.post(f"/api/catalog/{card_id}/link", json={
            "expected_version": 1, "source_ids": [seeds2["expert"]], "expert_ids": [], "work_ids": []})
        self.assertEqual(resp.status_code, 422)
        self.assertIn("source_ids", resp.get_json()["error"]["field_errors"])

        resp = self.client.post(f"/api/catalog/{card_id}/link", json={
            "expected_version": 1, "source_ids": [], "expert_ids": [], "work_ids": [seeds2["project"]]})
        self.assertEqual(resp.status_code, 422)
        self.assertIn("work_ids", resp.get_json()["error"]["field_errors"])

        resp = self.client.post(f"/api/catalog/{card_id}/link", json={
            "expected_version": 1, "source_ids": [seeds2["material"]], "expert_ids": [],
            "work_ids": [seeds2["work"]]})
        self.assertEqual(resp.status_code, 422)
        self.assertIn("work_ids", resp.get_json()["error"]["field_errors"])

    def test_link_then_ready_when_apprenticeship_complete(self):
        seeds = self._seed()
        card_id = self._create().get_json()["item"]["id"]
        resp = self.client.post(f"/api/catalog/{card_id}/link", json={
            "expected_version": 1, "source_ids": [seeds["material"]],
            "expert_ids": [seeds["expert"]], "work_ids": [seeds["work"]]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.get_json()["links"]["works"]), 1)
        self.assertEqual(resp.get_json()["card"]["status"], "hold")
        self.assertTrue(any("도제 학습 맥락" in r for r in resp.get_json()["card"]["hold_reasons"]))

        patch = self._patch(card_id, 2, apprentice_context="선임과 반복 실습",
                            novice_gap="초보자는 흐름 징후를 놓친다", tacit_signals=["표면 광택을 본다"],
                            evidence_refs=["src_note p.2"], axes={"origin": "현장 메모 3쪽"})
        self.assertEqual(patch.status_code, 200)
        self.assertEqual(patch.get_json()["card"]["status"], "ready")
        self.assertIn("수요", " ".join(patch.get_json()["card"]["limits"]))

    def test_version_conflict_returns_409(self):
        card_id = self._create().get_json()["item"]["id"]
        resp = self._patch(card_id, 99, summary="바뀐 요약")
        self.assertEqual(resp.status_code, 409)

    def test_list_and_get_unknown(self):
        before = self.client.get("/api/catalog/").get_json()["total"]
        card_id = self._create().get_json()["item"]["id"]
        listing = self.client.get("/api/catalog/").get_json()
        self.assertEqual(listing["total"], before + 1)
        mine = [i for i in listing["items"] if i["id"] == card_id][0]
        self.assertEqual(mine["card_status"], "hold")
        self.assertEqual(self.client.get("/api/catalog/?status=ready").get_json()["total"], 0)
        self.assertEqual(self.client.get("/api/catalog/cat_missing").status_code, 404)

    def test_non_json_is_rejected(self):
        resp = self.client.post("/api/catalog/", data="x", content_type="text/plain")
        self.assertEqual(resp.status_code, 415)


DRAFT = {"title": "AI 초안: 접합부 치수 판단", "domain": "목공", "summary": "접합부 치수 확인 판단",
         "judgement_task": "이 접합부가 허용 오차 안인지 판단", "target_capability": "치수 판정",
         "expected_deliverables": "판정 근거 1장"}


class CatalogAiDraftTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = create_app(ROOT, Path(self._tmp.name) / "catalog.db")
        self.client = self.app.test_client()

    def tearDown(self):
        self._tmp.cleanup()

    def _runs(self):
        with closing(db.connect(self.app.config["DB_PATH"])) as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM ai_draft_runs ORDER BY created_at").fetchall()]

    def test_not_adopted_returns_manual_without_calling_provider(self):
        env = {k: v for k, v in os.environ.items() if k not in ("FM_AI_ENABLED", "ANTHROPIC_API_KEY")}
        with unittest.mock.patch.dict(os.environ, env, clear=True), \
                unittest.mock.patch("urllib.request.urlopen") as net:
            status = self.client.get("/api/catalog/ai-status").get_json()
            data = self.client.post("/api/catalog/ai-draft", json={"source_ids": ["mat_seed01"]}).get_json()
        self.assertFalse(status["adopted"])
        self.assertIsNone(data["draft"])
        self.assertEqual(data["ai"]["label"], "수동 작성")
        net.assert_not_called()

    @unittest.skipIf(ai_fakes.REAL, "needs fake")
    def test_draft_linked_to_sources_is_unsaved_and_recorded(self):
        calls = ai_fakes.install(self, DRAFT)
        before = self.client.get("/api/catalog/").get_json()["total"]
        resp = self.client.post("/api/catalog/ai-draft", json={"source_ids": ["mat_seed01"]})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["draft"]["title"], DRAFT["title"])
        self.assertEqual(data["source_ids"], ["mat_seed01"])
        self.assertEqual(data["ai"]["label"], "AI 초안 (claude-test-1)")
        self.assertTrue(data["adoptable"])
        self.assertEqual(self.client.get("/api/catalog/").get_json()["total"], before)
        self.assertEqual(calls[0]["feature"], "F10.4")
        run = self._runs()[0]
        self.assertEqual((run["provider"], run["model"], run["ok"]), ("anthropic", "claude-test-1", 1))
        self.assertTrue(run["input_hash"] and run["started_at"] and run["finished_at"])
        self.assertNotIn("sk-", json.dumps(run))

    @unittest.skipIf(ai_fakes.REAL, "needs fake")
    def test_adopt_with_edit_creates_card_linked_and_labelled(self):
        ai_fakes.install(self, DRAFT)
        data = self.client.post("/api/catalog/ai-draft", json={"source_ids": ["mat_seed01"]}).get_json()
        edited = dict(data["draft"], title="사람이 고친 제목")
        resp = self.client.post(f"/api/catalog/ai-draft/{data['ai']['run_id']}/adopt", json=edited)
        self.assertEqual(resp.status_code, 201, resp.get_json())
        detail = resp.get_json()
        self.assertEqual(detail["item"]["title"], "사람이 고친 제목")
        self.assertEqual(detail["item"]["ai_draft"]["label"], "AI 초안 (claude-test-1)")
        self.assertEqual([l["id"] for l in detail["links"]["sources"]], ["mat_seed01"])
        self.assertEqual(detail["card"]["status"], "hold")  # draft adoption is not approval
        again = self.client.post(f"/api/catalog/ai-draft/{data['ai']['run_id']}/adopt", json=edited)
        self.assertEqual(again.status_code, 409)

    @unittest.skipIf(ai_fakes.REAL, "needs fake")
    def test_failure_and_garbage_fall_back_to_manual(self):
        ai_fakes.install(self, None)
        data = self.client.post("/api/catalog/ai-draft", json={"source_ids": ["mat_seed01"]}).get_json()
        self.assertIsNone(data["draft"])
        self.assertFalse(data["adoptable"])
        self.assertEqual(data["ai"]["label"], "수동 작성")
        self.assertEqual(self._runs()[-1]["ok"], 0)
        self.assertIn("boom", self._runs()[-1]["error"])
        ai_fakes.install(self, "not json at all")
        data = self.client.post("/api/catalog/ai-draft", json={"source_ids": ["mat_seed01"]}).get_json()
        self.assertIsNone(data["draft"])
        self.assertEqual(self._runs()[-1]["error"], "unparseable_response")

    def test_missing_source_rejected_and_manual_create_unchanged(self):
        ai_fakes.install(self, DRAFT)
        r = self.client.post("/api/catalog/ai-draft", json={"source_ids": ["mat_nope"]})
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/api/catalog/ai-draft", json={"source_ids": []})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.client.post("/api/catalog/", json=_card_payload()).status_code, 201)


if __name__ == "__main__":
    unittest.main()
