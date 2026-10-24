"""STORY 11.2/11.3 정적 점검: 반응형 기준 폭, 44px 조작 영역, 포커스 복귀, safe-area.

브라우저를 띄우지 않고 정적 파일만 읽는다. 실제 폭별 화면 확인은 별도 스크린샷 검수로 한다.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

FEATURE_JS = ["collect", "experts", "catalog", "rights", "refine", "fde", "company", "feedback"]


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def code_lines(text):
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith(("*", "/*", "//"))
    )


class ResponsiveStaticTests(unittest.TestCase):
    def test_index_loads_all_feature_scripts_and_styles(self):
        index = read("index.html")
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', index)
        for css in ["styles.css", "ui-forms.css", "s2-records.css", "collect.css", "feedback.css"]:
            self.assertIn(f'href="/static/{css}"', index)
        for js in FEATURE_JS + ["app", "ui-forms", "s2-records"]:
            self.assertIn(f'src="/static/{js}.js"', index)

    def test_breakpoints_cover_320_to_1440(self):
        css = read("styles.css") + read("ui-forms.css")
        for width in ["519px", "520px", "1023px", "1024px"]:
            self.assertIn(width, css, f"breakpoint {width} missing")

    def test_no_outline_removed_without_focus_replacement(self):
        for name in ["styles.css", "ui-forms.css", "feedback.css", "collect.css", "s2-records.css"]:
            code = code_lines(read(name))
            for match in re.finditer(r"outline\s*:\s*(none|0)\b", code):
                window = code[max(0, match.start() - 200):match.end() + 40]
                self.assertIn("focus", window, f"{name} removes outline outside a focus rule")

    def test_global_focus_visible_and_touch_targets(self):
        css = read("styles.css")
        self.assertIn(":focus-visible", css)
        self.assertRegex(css, r"button[^{]*\{[^}]*min-height:44px")
        self.assertRegex(css, r"input:not\(\[type=\"checkbox\"\]\)[^{]*select\{min-height:44px\}")

    def test_fm_controls_meet_44px(self):
        css = read("ui-forms.css")
        self.assertRegex(css, r"--fm-touch:\s*44px")
        self.assertRegex(css, r"\.fm-btn\s*\{[^}]*min-height:\s*var\(--fm-touch\)")
        self.assertRegex(css, r"\.fm-input\s*\{[^}]*min-height:\s*var\(--fm-touch\)")

    def test_safe_area_and_keyboard_space(self):
        css = read("styles.css") + read("ui-forms.css")
        self.assertIn("env(safe-area-inset-bottom", css)
        self.assertIn("env(safe-area-inset-bottom, 0px)", read("ui-forms.css"))
        self.assertIn("100dvh", read("ui-forms.css"))

    def test_fullscreen_dialog_on_phone_width(self):
        css = read("ui-forms.css")
        self.assertRegex(css, r"@media \(max-width: 520px\)\s*\{[^@]*\.fm-dialog\s*\{[^}]*100dvh")

    def test_dialogs_return_focus_to_opener(self):
        forms = code_lines(read("ui-forms.js"))
        self.assertIn("restoreFocus(opener)", forms)
        self.assertIn("options.opener", forms)
        feedback = code_lines(read("feedback.js"))
        self.assertIn("var opener = document.activeElement;", feedback)
        self.assertIn("done(true)", feedback)
        self.assertIn("opener.focus()", feedback)
        self.assertNotIn("dialog.close(); dialog.remove();", feedback)

    def test_form_event_passes_opener_not_body_text(self):
        app = code_lines(read("app.js"))
        self.assertIn("opener: formButton", app)
        forms = code_lines(read("ui-forms.js"))
        self.assertIn("opener !== document.body", forms)

    def test_feature_dialogs_use_shared_dialog_class(self):
        feedback = code_lines(read("feedback.js"))
        self.assertIn("'fm-dialog fb-dialog'", feedback)
        self.assertNotIn("className = 'fb-dialog'", feedback)

    def test_no_data_table_layout_in_feature_screens(self):
        # 표 대신 목록·카드로 전환해야 320px에서 가로 스크롤이 생기지 않는다.
        for name in [f"{n}.js" for n in FEATURE_JS]:
            code = code_lines(read(name))
            self.assertNotRegex(code, r"['\"]table['\"]", f"{name} builds a table")

    def test_stage_grid_collapses_on_phone(self):
        css = read("styles.css")
        self.assertRegex(css, r"@media \(max-width:519px\)\{[\s\S]*?\.stage-grid\{grid-template-columns:1fr\}")

    def test_no_horizontal_overflow_source_rules(self):
        css = read("styles.css")
        self.assertIn("min-width:0", css)
        self.assertIn("img,svg,video,canvas{max-width:100%", css)


if __name__ == "__main__":
    unittest.main()
