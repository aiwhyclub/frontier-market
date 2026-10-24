import tempfile
import unittest
from pathlib import Path

from backend.app import create_app

ROOT = Path(__file__).resolve().parent.parent


class FirstScreenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = create_app(ROOT, Path(self.tmp.name) / "test.sqlite3").test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_index_shows_product_name_and_synthetic_notice(self):
        res = self.client.get("/")
        body = res.get_data(as_text=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn("Frontier Market", body)
        self.assertIn("합성 데이터 시연", body)

    def test_health_reports_db(self):
        data = self.client.get("/api/health").get_json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["db_path"].endswith("test.sqlite3"))


class AppShellTest(unittest.TestCase):
    STAGES = ["dashboard", "s1", "s2", "s3", "s4", "s5", "s6", "s7"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = create_app(ROOT, Path(self.tmp.name) / "test.sqlite3").test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def _page(self):
        res = self.client.get("/")
        try:
            return res.get_data(as_text=True)
        finally:
            res.close()

    def test_shell_links_css_forms_css_and_dialog_root(self):
        body = self._page()
        self.assertIn('href="/static/styles.css"', body)
        self.assertIn('href="/static/ui-forms.css"', body)
        self.assertIn('id="dialog-root"', body)
        self.assertLess(body.index('id="dialog-root"'), body.index("</body>"))

    def test_scripts_load_app_then_ui_forms_as_deferred(self):
        body = self._page()
        app_tag = '<script src="/static/app.js" defer></script>'
        forms_tag = '<script src="/static/ui-forms.js" defer></script>'
        self.assertIn(app_tag, body)
        self.assertIn(forms_tag, body)
        self.assertLess(body.index(app_tag), body.index(forms_tag))

    def test_nav_lists_dashboard_and_seven_stage_hashes_with_names(self):
        body = self._page()
        for key in self.STAGES:
            self.assertIn(f'href="#{key}" data-screen="{key}"', body)
        for name in ["대시보드", "자료 수집", "DB", "전문가", "카탈로그", "정제·검증", "FDE", "가상회사"]:
            self.assertIn(name, body)

    def test_index_keeps_product_name_and_notice_visible_in_shell(self):
        body = self._page()
        self.assertIn('class="brand"', body)
        self.assertIn("Frontier Market", body)
        self.assertIn('class="sidebar-note"', body)
        self.assertIn("합성 데이터 시연", body)

    def test_viewport_meta_present_for_mobile_width(self):
        body = self._page()
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', body)

    def test_app_js_and_styles_are_served(self):
        js = self.client.get("/static/app.js")
        css = self.client.get("/static/styles.css")
        self.assertEqual(js.status_code, 200)
        self.assertEqual(css.status_code, 200)
        js_text = js.get_data(as_text=True)
        self.assertIn("hashchange", js_text)
        self.assertIn("aria-current", js_text)
        self.assertIn("fm:open-form", js_text)
        css.close()
        js.close()

    def test_styles_have_mobile_gutter_and_collapsed_nav(self):
        css = self.client.get("/static/styles.css")
        text = css.get_data(as_text=True)
        css.close()
        self.assertIn("--gutter:16px", text)
        self.assertIn("max-width:1023px", text)
        self.assertIn("min-height:44px", text)


if __name__ == "__main__":
    unittest.main()
