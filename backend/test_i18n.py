"""STORY 14.1 [F11.1]: English by default, Korean on request; error codes stay the same."""
import re
import tempfile
import unittest
from pathlib import Path

from backend import ai, i18n
from backend.app import create_app
from backend.i18n_testutil import TABLES

ROOT = Path(__file__).resolve().parent.parent


class ApiLanguageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = create_app(ROOT, Path(self.tmp.name) / "t.sqlite3").test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def _bad_record(self, **headers):
        return self.client.post("/api/records/materials", json={"title": "", "body": "x"}, headers=headers)

    def test_default_is_english_with_unchanged_code(self):
        res = self._bad_record()
        err = res.get_json()["error"]
        self.assertEqual(err["code"], "validation_error")
        self.assertEqual(err["message"], "Please check your input.")
        self.assertRegex(err["fields"]["title"], r"^The title must be a string of 1 to \d+ characters\.$")
        self.assertEqual(res.headers["Content-Language"], "en")

    def test_ko_header_returns_korean_and_same_code(self):
        err = self._bad_record(**{"X-FM-Lang": "ko"}).get_json()["error"]
        self.assertEqual(err["code"], "validation_error")
        self.assertEqual(err["message"], "입력을 확인해 주세요.")

    def test_accept_language_is_used_when_no_header(self):
        err = self._bad_record(**{"Accept-Language": "ko-KR,ko;q=0.9"}).get_json()["error"]
        self.assertEqual(err["message"], "입력을 확인해 주세요.")
        err = self._bad_record(**{"Accept-Language": "fr-FR"}).get_json()["error"]
        self.assertEqual(err["message"], "Please check your input.")

    def test_formatted_messages_are_translated(self):
        res = self.client.post("/api/records/materials", json={"title": "t" * 500, "body": "b", "synthetic": True})
        self.assertEqual(res.status_code, 400)
        self.assertRegex(res.get_json()["error"]["fields"]["title"], r"^The title must be a string of 1 to \d+ characters\.$")

    def test_not_found_and_record_data_untouched(self):
        self.assertEqual(self.client.get("/api/records/materials/nope").get_json()["error"]["message"], "Record not found.")
        items = self.client.get("/api/records/materials").get_json()["items"]
        self.assertTrue(any("가상" in item["title"] for item in items))  # seed data is never translated


class TranslateTextTest(unittest.TestCase):
    def test_templates_and_nested_labels(self):
        self.assertEqual(i18n.translate_text("{}자 이하로 입력해 주세요.".replace("{}", "80"), "en"), "Please enter 80 characters or fewer.")
        self.assertEqual(i18n.translate_text("발굴 6축 중 근거 없음·미확인: 제목, 분야", "en"),
                         "Discovery axes without evidence or unconfirmed: Title, Domain")
        self.assertEqual(i18n.translate_text("알 수 없는 문장", "en"), "알 수 없는 문장")
        self.assertEqual(i18n.translate_text("입력을 확인해 주세요.", "ko"), "입력을 확인해 주세요.")

    def test_every_template_keeps_its_placeholder_count(self):
        for ko, en in i18n.MESSAGES.items():
            if "{}" in ko:
                self.assertEqual(ko.count("{}"), len(re.findall(r"\{\d*\}", en)), ko)


class AiPromptLanguageTest(unittest.TestCase):
    def test_instruction_follows_request_language(self):
        app = create_app(ROOT, Path(tempfile.mkdtemp()) / "a.sqlite3")
        for header, word in (({}, "English"), ({"X-FM-Lang": "ko"}, "Korean")):
            with app.test_request_context("/", headers=header):
                self.assertIn(word, i18n.ai_language_instruction())

    def test_system_prompt_gets_language_line(self):
        app = create_app(ROOT, Path(tempfile.mkdtemp()) / "b.sqlite3")
        with app.test_request_context("/", headers={"X-FM-Lang": "ko"}):
            self.assertTrue(ai._with_language("SYS").endswith(i18n.AI_LANGUAGE_INSTRUCTION["ko"]))
        with app.test_request_context("/"):
            self.assertTrue(ai._with_language("SYS").endswith(i18n.AI_LANGUAGE_INSTRUCTION["en"]))


class UiDictionaryTest(unittest.TestCase):
    def test_en_and_ko_have_the_same_keys(self):
        self.assertEqual(set(TABLES["en"]), set(TABLES["ko"]))

    def test_every_key_used_in_static_files_exists(self):
        used = set()
        for path in [p for p in (ROOT / "static").glob("*.js") if p.name != "i18n.js"] + [ROOT / "static" / "index.html"]:
            text = path.read_text(encoding="utf-8")
            used |= set(re.findall(r"\bt\('([a-z0-9_.]+)'", text))
            used |= set(re.findall(r'data-i18n(?:-attrs)?="(?:[a-z-]+:)?([a-z0-9_.]+)"', text))
        self.assertFalse(sorted(k for k in used if k not in TABLES["en"] and True))

    def test_no_hardcoded_korean_in_ui_code(self):
        for path in (ROOT / "static").glob("*.js"):
            if path.name == "i18n.js":
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("//")[0] if "//" in line else line
                stripped = line.lstrip()
                if stripped.startswith(("*", "/*", "//")) or not re.search("[가-힣]", code):
                    continue
                # the decision field intentionally also accepts the original Korean words
                self.assertTrue("'승인'" in code or "'예'" in code, f"{path.name}:{number}: {line.strip()}")

    def test_index_defaults_to_english_with_language_switch(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="en">', html)
        self.assertIn('id="lang-switch"', html)
        self.assertLess(html.index("/static/i18n.js"), html.index("/static/demo.js"))
        self.assertNotRegex(html, "[가-힣]")


if __name__ == "__main__":
    unittest.main()
