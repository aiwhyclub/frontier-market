import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.app import create_app

ROOT = Path(__file__).resolve().parent.parent
JS_PATH = ROOT / "static" / "ui-forms.js"
CSS_PATH = ROOT / "static" / "ui-forms.css"


class FormsAssetTest(unittest.TestCase):
    """STORY 1.3: 등록 폼·상세·상태 안내의 공통 틀이 정적 자원으로 제공되고 규칙을 담고 있는지 확인한다."""

    @classmethod
    def setUpClass(cls):
        cls.js = JS_PATH.read_text(encoding="utf-8")
        cls.css = CSS_PATH.read_text(encoding="utf-8")
        # 주석 줄을 제외한 코드만 검사한다 (규칙 설명 주석에 금지어가 등장할 수 있다).
        cls.js_code = "\n".join(
            line for line in cls.js.splitlines()
            if not line.lstrip().startswith(("*", "/*", "//"))
        )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = create_app(ROOT, Path(self.tmp.name) / "test.sqlite3").test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_assets_are_served_from_static(self):
        js = self.client.get("/static/ui-forms.js")
        css = self.client.get("/static/ui-forms.css")
        try:
            self.assertEqual(js.status_code, 200)
            self.assertEqual(css.status_code, 200)
            self.assertIn("javascript", js.content_type)
            self.assertIn("text/css", css.content_type)
        finally:
            js.close()
            css.close()

    def test_global_api_is_exposed(self):
        self.assertIn("window.FMForms", self.js)
        for name in ("openForm", "openDetail", "showToast"):
            self.assertIn(name, self.js)

    def test_dialog_root_and_demo_button_are_wired_by_id(self):
        self.assertIn("dialog-root", self.js)
        self.assertIn("forms-demo-button", self.js)
        self.assertIn("DOMContentLoaded", self.js)

    def test_native_dialog_escape_focus_return_and_trap(self):
        self.assertIn("showModal", self.js)
        self.assertIn("'cancel'", self.js)  # Esc 처리
        self.assertIn("trapTab", self.js)  # 포커스 가둠
        self.assertIn("restoreFocus", self.js)  # 호출 위치 복원

    def test_labels_are_bound_to_inputs(self):
        self.assertIn("'for': inputId", self.js)
        self.assertIn("aria-describedby", self.js)
        self.assertIn("aria-required", self.js)

    def test_saving_state_disables_submit_and_shows_message(self):
        self.assertIn("저장 중…", self.js)
        self.assertIn("submit.disabled = on", self.js)
        self.assertIn("if (saving) return;", self.js)

    def test_error_region_is_aria_live_and_keeps_input(self):
        self.assertIn("'aria-live': 'assertive'", self.js)
        # 실패 경로에서 입력값을 지우는 코드가 없어야 한다.
        failure = self.js.split("function (err) {", 1)[1].split("});", 1)[0]
        self.assertNotIn(".value = ''", failure)
        self.assertNotIn("reset()", failure)

    def test_error_message_never_exposes_raw_error(self):
        self.assertIn("err.userMessage", self.js_code)
        self.assertNotIn("err.message", self.js_code)
        self.assertNotIn("err.stack", self.js_code)

    def test_success_toast_is_polite_status(self):
        self.assertIn("'aria-live': 'polite'", self.js)
        self.assertIn("role: 'status'", self.js)
        self.assertIn("저장했어요", self.js)

    def test_no_browser_storage_is_used(self):
        self.assertNotIn("localStorage", self.js_code)
        self.assertNotIn("sessionStorage", self.js_code)

    def test_css_has_focus_touch_target_and_safe_area(self):
        self.assertIn(":focus-visible", self.css)
        self.assertIn("--fm-touch: 44px", self.css)
        self.assertIn("env(safe-area-inset-bottom", self.css)
        self.assertIn("@media (max-width: 520px)", self.css)
        self.assertIn("font-size: 16px", self.css)

    def test_css_uses_design_tokens(self):
        for token in ("#172c3a", "#1e6b60", "#a63b36", "#f7f5ef"):
            self.assertIn(token, self.css)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_passes_node_syntax_check(self):
        result = subprocess.run(
            ["node", "--check", str(JS_PATH)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
