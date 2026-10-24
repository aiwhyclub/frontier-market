import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.app import create_app
from backend.i18n_testutil import tk

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
JS_PATH = STATIC / "s2-records.js"
CSS_PATH = STATIC / "s2-records.css"
APP_JS_PATH = STATIC / "app.js"
INDEX_PATH = STATIC / "index.html"

RECORD_TYPES = ["materials", "experts", "works", "catalog", "projects", "companies", "rights", "feedback"]


def code_lines(text):
    """주석 줄을 뺀 코드만 돌려준다. 규칙 설명 주석에 금지어가 등장할 수 있다."""
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith(("*", "/*", "//"))
    )


class S2RecordsAssetTest(unittest.TestCase):
    """STORY 2.4 [F2.2]: S2 유형별 목록·원문·연결 상세와 빈 상태 화면의 정적 자원과 계약을 확인한다."""

    @classmethod
    def setUpClass(cls):
        cls.js = JS_PATH.read_text(encoding="utf-8")
        cls.js_code = code_lines(cls.js)
        cls.css = CSS_PATH.read_text(encoding="utf-8")
        cls.app_js = APP_JS_PATH.read_text(encoding="utf-8")
        cls.index = INDEX_PATH.read_text(encoding="utf-8")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = create_app(ROOT, Path(self.tmp.name) / "test.sqlite3").test_client()

    def tearDown(self):
        self.tmp.cleanup()

    # 자원 제공과 연결

    def test_assets_are_served_from_static(self):
        js = self.client.get("/static/s2-records.js")
        css = self.client.get("/static/s2-records.css")
        self.assertEqual(js.status_code, 200)
        self.assertEqual(css.status_code, 200)
        self.assertIn("javascript", js.content_type)
        self.assertIn("text/css", css.content_type)

    def test_index_includes_css_and_script_after_app(self):
        self.assertIn('href="/static/s2-records.css"', self.index)
        self.assertIn('src="/static/s2-records.js"', self.index)
        self.assertLess(self.index.index("/static/app.js"), self.index.index("/static/s2-records.js"))

    def test_app_hook_mounts_screen_only_when_module_is_loaded(self):
        self.assertIn("window.FMScreens[key](document.getElementById('screen-root'))", self.app_js)
        self.assertIn("var mounts = hasScreen(key);", self.app_js)
        # 스크립트가 없을 때도 기본 빈 상태가 남아 있어야 한다.
        self.assertIn("nextActionHtml(s.next)", self.app_js)

    def test_js_passes_node_syntax_check(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        result = subprocess.run(
            ["node", "--check", str(JS_PATH)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    # 안전 규칙: 서버 문자열은 DOM API로만 넣는다

    def test_no_html_injection_apis(self):
        for banned in ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"]:
            self.assertNotIn(banned, self.js_code, banned)

    def test_no_browser_storage(self):
        for banned in ["localStorage", "sessionStorage", "indexedDB"]:
            self.assertNotIn(banned, self.js_code, banned)

    def test_server_text_uses_text_content(self):
        self.assertIn("node.textContent = String(value)", self.js_code)

    def test_raw_error_details_are_not_rendered(self):
        # 오류 문구는 HTTP 상태에 맞춘 고정 안내만 쓴다. err.message나 서버 error.message를 화면에 넣지 않는다.
        self.assertNotIn("err.message", self.js_code)
        self.assertNotIn("error.message", self.js_code)
        self.assertNotIn(".stack", self.js_code)

    # API 계약

    def test_uses_contract_endpoints(self):
        self.assertIn("'/api/records/' + encodeURIComponent(type)", self.js_code)
        self.assertIn("'/api/records/' + encodeURIComponent(type) + '/' + encodeURIComponent(id)", self.js_code)

    def test_reads_contract_fields(self):
        for field in ["items", "item", "links", "missing_links", "material_hash", "input_hash", "synthetic", "updated_at"]:
            self.assertIn(field, self.js_code, field)

    def test_all_eight_record_types_are_offered(self):
        for key in RECORD_TYPES:
            self.assertIn("key: '%s'" % key, self.js_code, key)

    # 화면 요구사항

    def test_type_switcher_is_labelled_native_select(self):
        self.assertIn("el('select', { id: 's2-type'", self.js_code)
        self.assertIn("el('label', { 'for': 's2-type'", self.js_code)
        self.assertIn(tk("기록 유형"), self.js_code)

    def test_list_shows_synthetic_badge_and_status(self):
        self.assertIn("MSG.synthetic", self.js_code)
        self.assertIn(tk("상태") + " + ' ' + item.status", self.js_code)

    def test_detail_separates_original_and_version_hashes(self):
        self.assertIn(tk("원문"), self.js_code)
        self.assertIn(tk("원문 해시"), self.js_code)
        self.assertIn(tk("입력 해시"), self.js_code)
        self.assertIn("definition(" + tk("버전") + ", item.version)", self.js_code)

    def test_links_open_their_record_and_show_missing_reasons(self):
        self.assertIn("selectRecord(linkType, linkId)", self.js_code)
        self.assertIn("miss.reason", self.js_code)
        self.assertIn(tk("연결되지 않은 항목"), self.js_code)

    def test_empty_state_dispatches_existing_form_event(self):
        self.assertIn("new CustomEvent('fm:open-form'", self.js_code)
        self.assertIn("detail: { form: guide.form }", self.js_code)
        # app.js의 위임 핸들러가 같은 버튼에서 한 번 더 이벤트를 보내지 않도록 data-form을 쓰지 않는다.
        self.assertNotIn("'data-form'", self.js_code)

    def test_empty_state_offers_stage_link_when_no_form(self):
        self.assertIn("el('a', { className: 'btn btn-primary', href: guide.href", self.js_code)

    def test_loading_and_error_states_are_announced(self):
        self.assertIn(tk("불러오는 중…"), self.js_code)
        self.assertIn("role: kind === 'error' ? 'alert' : 'status'", self.js_code)
        self.assertIn(tk("다시 시도"), self.js_code)
        self.assertIn(tk("서버에 연결하지 못했어요. 인터넷 연결을 확인한 뒤 다시 시도해 주세요. 화면의 선택은 그대로 있어요."), self.js_code)

    def test_stale_responses_are_ignored(self):
        self.assertIn("token !== state.listToken", self.js_code)
        self.assertIn("token !== state.detailToken", self.js_code)

    def test_search_form_has_text_field_status_inputs_and_submit_only_reload(self):
        self.assertIn("el('form', { className: 's2-search', role: 'search'", self.js_code)
        self.assertIn("type: 'search'", self.js_code)
        self.assertIn("el('label', { 'for': 's2-q'", self.js_code)
        self.assertIn("el('label', { 'for': 's2-field'", self.js_code)
        self.assertIn("el('label', { 'for': 's2-status'", self.js_code)
        self.assertIn("event.preventDefault();", self.js_code)
        self.assertIn("maxlength: String(QUERY_MAX)", self.js_code)

    def test_search_params_are_sent_to_the_contract_endpoint_only_when_filled(self):
        self.assertIn("params.set(key, state.query[key])", self.js_code)
        self.assertIn("encodeURIComponent(type) + searchQueryString()", self.js_code)
        self.assertIn("['q', 'field', 'status']", self.js_code)

    def test_empty_search_state_is_distinct_from_first_registration_empty_state(self):
        self.assertIn("t('s2.no_matching_records', { label: info.label })", self.js_code)
        self.assertIn("isSearching() ? searchEmptyBlock(info) : emptyBlock(info)", self.js_code)
        self.assertIn(tk("검색 지우기"), self.js_code)

    def test_search_query_survives_detail_round_trip_via_state(self):
        self.assertIn("query: { q: '', field: '', status: '' }", self.js_code)
        self.assertIn("syncSearchInputs()", self.js_code)

    def test_search_css_keeps_targets_and_no_horizontal_overflow(self):
        self.assertIn(".s2-input{min-height:44px", self.css)
        self.assertIn("max-width:100%", self.css)
        self.assertNotIn("100vw", self.css)

    def test_keyboard_controls_are_native_buttons(self):
        self.assertIn("el('button', {\n        type: 'button', className: 's2-card-button'", self.js_code)
        self.assertIn("className: 's2-link-button'", self.js_code)

    # 스타일 규칙

    def test_css_keeps_44px_targets_and_gutter_without_extra_side_padding(self):
        self.assertIn("min-height:44px", self.css)
        self.assertNotIn("100vw", self.css)
        self.assertIn("overflow-wrap:anywhere", self.css)
        self.assertIn("max-width:100%", self.css)

    def test_css_switches_list_and_detail_at_tablet_breakpoint(self):
        self.assertIn("@media (max-width:1023px)", self.css)
        self.assertIn('.s2-layout[data-view="detail"] .s2-list{display:none}', self.css)
        self.assertIn("@media (min-width:1024px)", self.css)


if __name__ == "__main__":
    unittest.main()
