"""Request-language support for API messages (EPIC 14, F11.1).

The server code keeps its original Korean message text as the catalog key. The language for a request
comes from the `X-FM-Lang` header the UI sends (or `Accept-Language`), and defaults to English.
Korean requests get the text unchanged; English requests get the text from MESSAGES below.

- error codes (`error.code`) and all data values are never touched;
- an after_request hook translates the human-readable strings of JSON responses (error messages,
  validation field messages, notices, labels) whose text matches a catalog entry exactly or one of
  the `{}` templates; record content (title, body, fields) is user/seed data and is never translated;
- `tr()` translates text that the server *generates and stores* (draft text) at creation time.
"""
import re

from flask import g, has_request_context, request

DEFAULT_LANG = "en"
SUPPORTED = ("en", "ko")

# Keys whose subtree is user or seed data: never translated. (`fields` is data on records but holds
# the per-field messages inside an `error` object, handled in _walk.)
DATA_KEYS = frozenset({"title", "body", "fields", "name", "text", "notes", "objective", "scope",
                       "deliverables", "owner", "reviewer", "holder", "company_name", "summary"})

AI_LANGUAGE_INSTRUCTION = {
    "en": "Write every free-text value of your answer in English.",
    "ko": "Write every free-text value of your answer in Korean (한국어).",
}


def _normalize(tag):
    tag = (tag or "").strip().lower()
    for lang in SUPPORTED:
        if tag == lang or tag.startswith(lang + "-") or tag.startswith(lang + "_"):
            return lang
    return None


def request_lang():
    """Language of the current request: X-FM-Lang, then Accept-Language, then English."""
    if not has_request_context():
        return DEFAULT_LANG
    cached = g.get("fm_lang")
    if cached:
        return cached
    lang = _normalize(request.headers.get("X-FM-Lang"))
    if lang is None:
        for part in (request.headers.get("Accept-Language") or "").split(","):
            lang = _normalize(part.split(";")[0])
            if lang:
                break
    g.fm_lang = lang or DEFAULT_LANG
    return g.fm_lang


def ai_language_instruction(lang=None):
    return AI_LANGUAGE_INSTRUCTION[lang or request_lang()]


_EXACT = {}
_PATTERNS = []


def _compile():
    for ko, en in MESSAGES.items():
        if "{}" not in ko:
            _EXACT[ko] = en
            continue
        parts = ko.split("{}")
        regex = "(.+?)".join(re.escape(p) for p in parts)
        weight = sum(len(p) for p in parts)
        _PATTERNS.append((weight, re.compile(regex, re.S), en))
    _PATTERNS.sort(key=lambda item: -item[0])


def translate_text(text, lang=None):
    """Return `text` in the request language. Unknown text is returned unchanged."""
    lang = lang or request_lang()
    if lang == "ko" or not isinstance(text, str) or not text:
        return text
    found = _EXACT.get(text)
    if found is not None:
        return found
    for _weight, pattern, en in _PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            args = [_translate_part(group) for group in match.groups()]
            return en.format(*args)
    return text


def _translate_part(part):
    """A captured argument may itself be catalog text, or a ', '-joined list of it."""
    out = translate_text(part, "en")
    if out != part:
        return out
    if ", " in part:
        pieces = part.split(", ")
        translated = [translate_text(p, "en") for p in pieces]
        if translated != pieces:
            return ", ".join(translated)
    return part


def tr(ko, *args):
    """Translate server-generated text at creation time (`ko` is the Korean template with `{}`)."""
    lang = request_lang()
    if lang == "ko":
        return ko.format(*args) if args and "{}" in ko else ko
    template = MESSAGES.get(ko, ko)
    return template.format(*args) if args and "{}" in ko else template


def _walk(value, lang, in_error=False):
    """`in_error`: this dict is an `error` object, whose `fields` map field name -> message."""
    if isinstance(value, str):
        return translate_text(value, lang)
    if isinstance(value, list):
        return [_walk(v, lang) for v in value]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "error":
                out[k] = _walk(v, lang, True)
            elif k == "fields" and in_error and isinstance(v, dict):
                out[k] = {name: _walk(msg, lang) for name, msg in v.items()}
            elif k in DATA_KEYS:
                out[k] = v
            else:
                out[k] = _walk(v, lang)
        return out
    return value


def translate_response(response):
    """after_request hook: translate JSON API messages to the request language."""
    response.headers.add("Vary", "X-FM-Lang, Accept-Language")
    lang = request_lang()
    response.headers["Content-Language"] = lang
    if lang == "ko" or response.mimetype != "application/json" or response.direct_passthrough:
        return response
    try:
        payload = response.get_json(silent=True)
    except Exception:
        return response
    if payload is None:
        return response
    import json
    translated = _walk(payload, lang)
    if translated != payload:
        response.set_data(json.dumps(translated, ensure_ascii=False) + "\n")
    return response


def install(app):
    app.after_request(translate_response)


# Korean source text -> English. `{}` marks a formatted value, in order of appearance.
MESSAGES = {
    "권리 기록이 없어 학습·평가 이용 범위가 미확인입니다.":
        "There is no rights record, so the training and evaluation usage scope is unconfirmed.",
    "작업을 올린 전문가가 연결되지 않았습니다.":
        "The expert who uploaded the work is not linked.",
    "원문 자료가 연결되지 않았습니다.":
        "The source material is not linked.",
    "카탈로그가 아직 없어 연결되지 않았습니다. 카탈로그를 만든 뒤 연결할 수 있습니다.":
        "There is no catalog yet, so nothing is linked. Create a catalog first, then link it.",
    "근거 자료가 연결되지 않았습니다.":
        "The evidence material is not linked.",
    "연결된 원시 작업이 없습니다.":
        "There is no linked raw work.",
    "필수 카탈로그 연결이 없습니다.":
        "The required catalog link is missing.",
    "생성 당시 참조할 작업이 없습니다.":
        "There is no work to reference at creation time.",
    "필수 프로젝트 연결이 없습니다.":
        "The required project link is missing.",
    "권리 대상(자료 또는 전문가)이 연결되지 않았습니다.":
        "The rights subject (material or expert) is not linked.",
    "연결 대상이 없습니다. 보완 대상은 나중에 연결할 수 있습니다.":
        "There is no link target. A supplement target can be linked later.",
    "JSON 객체를 보내세요.":
        "Please send a JSON object.",
    "task_id가 필요합니다.":
        "task_id is required.",
    "expected_run_version(0 이상의 정수)이 필요합니다.":
        "expected_run_version (an integer of 0 or more) is required.",
    "action은 request_inspection/hold/release 중 하나입니다.":
        "action must be one of request_inspection/hold/release.",
    "공개 대상이 아닙니다.":
        "This is not a public target.",
    "찾을 수 없습니다.":
        "Not found.",
    "같은 시도가 동시에 바뀌었습니다. 새로고침 후 다시 시도하세요.":
        "The same attempt was changed at the same time. Refresh and try again.",
    "공개 시연 회사를 찾을 수 없습니다.":
        "The public demo company was not found.",
    "현재 권리·검토 조건으로 시연이 보류되었습니다.":
        "The demo is on hold because of the current rights and review conditions.",
    "회사에 없는 과제입니다.":
        "This task does not belong to the company.",
    "이 세션의 시연 한도에 도달했습니다.":
        "This session has reached its demo limit.",
    "실행 버전이 바뀌었습니다. 새로고침 후 다시 시도하세요.":
        "The run version changed. Refresh and try again.",
    "통과 항목 수 0~4":
        "Checks passed: 0-4",
    "이미 해제된 과제입니다. 초기화한 뒤 새 시도로 진행하세요.":
        "This task is already released. Reset it and continue with a new attempt.",
    "검사 전 해제는 거절됩니다. 먼저 검사를 요청하세요.":
        "Release before inspection is refused. Request an inspection first.",
    "다시 시도하세요.":
        "Please try again.",
    "같은 요청 번호로 다른 내용이 이미 저장되었습니다.":
        "Different content was already saved under the same request number.",
    "AI 초안 ({})":
        "AI draft ({})",
    "수동 작성":
        "Written manually",
    "수동 작성 (AI 미채택)":
        "Written manually (AI not adopted)",
    "제목":
        "Title",
    "분야":
        "Domain",
    "요약":
        "Summary",
    "구매할 판단 과제":
        "Judgement task to buy",
    "대상 능력":
        "Target capability",
    "예상 납품물":
        "Expected deliverables",
    "도제 학습 맥락":
        "Apprenticeship learning context",
    "초보자 차이":
        "Novice differences",
    "숙련 단서":
        "Expert cues",
    "근거":
        "Evidence",
    "도제 경험 기원":
        "Origin in apprenticeship experience",
    "비표준/문서화 공백":
        "Non-standard / undocumented gap",
    "현장 기본 필요성/오판 비용":
        "On-site baseline need / cost of misjudgement",
    "숙련자-초보자 차이":
        "Expert vs novice difference",
    "결과 검증 가능성":
        "Verifiability of results",
    "Lab 평가 능력 연결":
        "Link to Lab-evaluated capability",
    "입력을 확인해 주세요.":
        "Please check your input.",
    "문자열이어야 합니다.":
        "Must be a string.",
    "필수 항목입니다. 공백만 입력할 수 없습니다.":
        "Required. Whitespace alone is not allowed.",
    "{}자 이하로 입력해 주세요.":
        "Please enter {} characters or fewer.",
    "문자열 목록이어야 합니다.":
        "Must be a list of strings.",
    "최대 {}개, 항목당 {}자까지입니다.":
        "Up to {} items, {} characters each.",
    "매체 참조는 최대 {}개 목록이어야 합니다.":
        "Media references must be a list of at most {} items.",
    "발굴 축 키가 올바르지 않습니다.":
        "The discovery axis key is not valid.",
    "true 또는 false여야 합니다.":
        "Must be true or false.",
    "제목이 대상 능력과 같습니다. 단순 기술명 대신 구매할 판단 과제를 적어 주세요.":
        "The title is the same as the target capability. Write the judgement task to buy instead of a plain skill name.",
    "발굴 점수와 준비 상태는 전문성·수요·권리 승인이 아닙니다.":
        "Discovery scores and readiness are not expertise, demand or rights approval.",
    "ID 문자열 목록이어야 합니다.":
        "Must be a list of ID strings.",
    "최대 {}개까지 연결할 수 있습니다.":
        "You can link up to {} items.",
    "정수여야 합니다.":
        "Must be an integer.",
    "필수 항목입니다.":
        "Required.",
    "{}번째 매체 참조 형식이 올바르지 않습니다.":
        "Media reference #{} has an invalid format.",
    "{}번째 매체의 주소는 http(s):// 로 시작해야 합니다.":
        "The address of media #{} must start with http(s)://.",
    "{}번째 매체는 시점과 설명이 모두 필요합니다.":
        "Media #{} needs both a time and a description.",
    "각 축은 근거 문자열 또는 '미확인'이어야 합니다.":
        "Each axis must be an evidence string or 'Unconfirmed'.",
    "연결된 원천 자료가 없습니다. S2에서 자료를 확인해 연결하세요.":
        "There is no linked source material. Check materials in S2 and link them.",
    "승인된 전문가가 연결되지 않았습니다.":
        "No approved expert is linked.",
    "근거 참조 또는 매체 참조가 필요합니다.":
        "An evidence reference or media reference is required.",
    "매체 참조는 주소·시점·설명 메타데이터입니다. 자동 영상 분석은 수행되지 않았습니다.":
        "Media references are address, time and description metadata. No automatic video analysis was performed.",
    "초안의 원천 자료를 1개 이상 선택해 주세요.":
        "Select at least one source material for the draft.",
    "AI 초안은 저장되지 않았습니다. 읽고 수정한 뒤 채택해야 카드가 만들어집니다. 수동 작성은 언제든 쓸 수 있습니다.":
        "The AI draft is not saved. Read and edit it, then adopt it to create the card. You can always write manually.",
    "채택할 수 있는 AI 초안을 찾을 수 없습니다.":
        "No adoptable AI draft was found.",
    "expected_version이 필요합니다.":
        "expected_version is required.",
    "연결 입력을 확인해 주세요.":
        "Please check the link input.",
    "{}번째 줄은 '주소 | 시점 | 설명' 형식이어야 합니다.":
        "Line {} must be in the format 'URL | time | description'.",
    "{} 입력 필요":
        "{} required",
    "{}이(가) 비어 있습니다.":
        "{} is empty.",
    "발굴 6축 중 근거 없음·미확인: {}":
        "Discovery axes without evidence or unconfirmed: {}",
    "{}: 존재하지 않는 자료입니다.":
        "{}: this material does not exist.",
    "{}: 존재하지 않는 전문가입니다.":
        "{}: this expert does not exist.",
    "{}: 승인되지 않은 전문가는 연결할 수 없습니다.":
        "{}: an unapproved expert cannot be linked.",
    "{}: 존재하지 않는 작업이거나 작업 유형이 아닙니다.":
        "{}: this work does not exist or is not a work record.",
    "{}: 작업을 올린 전문가가 연결되어 있지 않습니다.":
        "{}: the expert who uploaded the work is not linked.",
    "application/json 요청만 받습니다.":
        "Only application/json requests are accepted.",
    "JSON 객체를 보내 주세요.":
        "Please send a JSON object.",
    "limit/offset은 정수여야 합니다.":
        "limit/offset must be integers.",
    "AI 초안을 사람이 검토·수정해 채택한 카드입니다. 권리·수요·전문성 승인이 아닙니다.":
        "A card adopted after a person reviewed and edited the AI draft. It is not rights, demand or expertise approval.",
    "과제 카드를 찾을 수 없습니다.":
        "Task card not found.",
    "{}: 승인되지 않은 전문가의 작업은 연결할 수 없습니다.":
        "{}: work by an unapproved expert cannot be linked.",
    "{}: 작업의 전문가({})가 전문가 목록에 없습니다.":
        "{}: the work's expert ({}) is not in the expert list.",
    "{}의 원문 자료({})가 자료 목록에 없습니다.":
        "The source material ({1}) of {0} is not in the material list.",
    "이미 채택한 AI 초안입니다.":
        "This AI draft was already adopted.",
    "자료 ID 목록이 필요합니다.":
        "A list of material IDs is required.",
    "맞지 않는 연결이 있어 저장하지 않았습니다.":
        "Some links do not fit, so nothing was saved.",
    "다른 변경이 먼저 저장되었습니다(현재 버전 {}). 다시 열어 주세요.":
        "Another change was saved first (current version {}). Please reopen.",
    "다른 변경이 먼저 저장되었습니다. 다시 열어 주세요.":
        "Another change was saved first. Please reopen.",
    "{}자 이하 문자열이어야 합니다.":
        "Must be a string of {} characters or fewer.",
    "허용되지 않은 항목입니다.":
        "This field is not allowed.",
    "본문을 입력해 주세요.":
        "Please enter the text.",
    "현재는 합성 자료만 등록할 수 있습니다(true).":
        "Only synthetic material can be registered for now (true).",
    "adopt 또는 correct 중 하나여야 합니다.":
        "Must be either adopt or correct.",
    "주소 형식을 확인해 주세요. 공백 없이 전체 주소를 입력해 주세요.":
        "Please check the address format. Enter the full address without spaces.",
    "http 또는 https 주소만 가져올 수 있습니다.":
        "Only http or https addresses can be fetched.",
    "주소 형식을 확인해 주세요. 계정 정보가 들어간 주소는 사용할 수 없습니다.":
        "Please check the address format. Addresses containing account information cannot be used.",
    "80 또는 443 포트의 공개 주소만 가져올 수 있습니다.":
        "Only public addresses on port 80 or 443 can be fetched.",
    "내부 또는 비공개 주소는 수집할 수 없습니다.":
        "Internal or private addresses cannot be collected.",
    "주소를 찾지 못했습니다. 주소를 확인하거나 본문을 직접 붙여 넣어 주세요.":
        "The address could not be found. Check the address or paste the text directly.",
    "HTML·텍스트·JSON 자료만 가져올 수 있습니다. PDF·영상·로그인 페이지는 본문을 직접 붙여 넣어 주세요.":
        "Only HTML, text and JSON can be fetched. Paste the text of PDFs, videos and login pages directly.",
    "자료가 500KiB를 넘어 가져오지 않았습니다. 필요한 부분을 본문으로 붙여 넣어 주세요.":
        "The material is over 500 KiB and was not fetched. Paste the part you need as text.",
    "읽을 수 있는 본문이 없습니다. 본문을 직접 붙여 넣어 주세요.":
        "There is no readable text. Please paste the text directly.",
    "본문이 10만 자를 넘어 가져오지 않았습니다. 일부만 본문으로 붙여 넣어 주세요.":
        "The text is over 100,000 characters and was not fetched. Paste only part of it as text.",
    "리다이렉트가 5회를 넘어 가져오지 않았습니다.":
        "More than 5 redirects, so it was not fetched.",
    "가져올 주소를 입력해 주세요.":
        "Please enter the address to fetch.",
    "본문은 {}자 이하여야 합니다.":
        "Text must be {} characters or fewer.",
    "개정본을 찾을 수 없습니다.":
        "Revision not found.",
    "교정한 정리본을 입력해 주세요.":
        "Please enter the corrected organized version.",
    "주소 형식을 확인해 주세요.":
        "Please check the address format.",
    "10초 안에 응답이 없어 가져오지 못했습니다. 다시 요청하거나 본문을 직접 붙여 넣어 주세요.":
        "No response within 10 seconds, so it could not be fetched. Try again or paste the text directly.",
    "연결하지 못했습니다. 다시 요청하거나 본문을 직접 붙여 넣어 주세요.":
        "Could not connect. Try again or paste the text directly.",
    "application/json 으로 보내 주세요.":
        "Please send as application/json.",
    "요청이 1MiB를 넘었습니다.":
        "The request is over 1 MiB.",
    "정수여야 합니다. 화면을 새로고침한 뒤 다시 저장해 주세요.":
        "Must be an integer. Refresh the screen and save again.",
    "원천 자료 {} 개정 v{}: {}":
        "Source material {} revision v{}: {}",
    "정리본은 {}자 이하여야 합니다.":
        "The organized version must be {} characters or fewer.",
    "자료를 찾을 수 없습니다.":
        "Material not found.",
    "다른 곳에서 먼저 저장되었습니다. 최신 내용을 다시 읽은 뒤 저장해 주세요.":
        "It was saved elsewhere first. Reload the latest content and save again.",
    "저장하지 못했습니다. 입력은 그대로 두었으니 다시 시도해 주세요.":
        "Could not save. Your input is kept, so please try again.",
    "원문을 비워 둘 수 없습니다.":
        "The original text cannot be left empty.",
    "요약: ":
        "Summary: ",
    "원문이 바뀌어 초안이 오래되었습니다. 초안을 다시 만든 뒤 채택해 주세요.":
        "The original text changed, so the draft is outdated. Create a new draft and then adopt it.",
    "문자 코드를 읽을 수 없습니다. 본문을 직접 붙여 넣어 주세요.":
        "The character encoding could not be read. Please paste the text directly.",
    "서버가 HTTP {} 응답을 주어 가져오지 못했습니다.":
        "The server responded with HTTP {}, so it could not be fetched.",
    "원문은 {}자 이하여야 합니다.":
        "The original text must be {} characters or fewer.",
    "원문이 없어 초안을 만들 수 없습니다.":
        "There is no original text, so a draft cannot be created.",
    "먼저 정리 초안을 만들어 주세요.":
        "Please create an organized draft first.",
    "리다이렉트 위치가 없어 가져오지 못했습니다.":
        "The redirect had no location, so it could not be fetched.",
    "원문":
        "Original text",
    "AI 초안을 사람이 검토·수정해 채택한 문서입니다. 권리·사람 검증 게이트를 통과한 작업만 근거이며, 실제 모델 훈련을 수행했다는 뜻이 아닙니다.":
        "A document adopted after a person reviewed and edited the AI draft. Only work that passed the rights and human-verification gates is used as evidence; it does not mean a model was actually trained.",
    "절차 순서 확인":
        "Procedure order check",
    "근거 연결":
        "Evidence linking",
    "위험 판단 설명":
        "Risk judgement explanation",
    "검토자 승인 확인":
        "Reviewer approval check",
    "'{}' 목적의 이용 범위가 부족합니다.":
        "The usage scope for purpose '{}' is insufficient.",
    "상태 {}":
        "Status {}",
    "기간 만료":
        "Period expired",
    "현재 내용에 대한 권리가 유효하지 않습니다 ({}).":
        "The rights for the current content are not valid ({}).",
    "권리 기록이 없거나 현재 내용에 대해 미확인입니다.":
        "There is no rights record, or it is unconfirmed for the current content.",
    "평가 전용 범위입니다. 평가 전용 자료는 학습 허용으로 바뀌지 않습니다.":
        "This is an evaluation-only scope. Evaluation-only material does not become training-allowed.",
    "합성 규칙 환경(외부 실행 없음)":
        "Synthetic rule environment (no external execution)",
    "초안(draft) 객체가 필요합니다.":
        "A draft object is required.",
    "초안 내용이 비어 있습니다.":
        "The draft content is empty.",
    "로컬 다운로드입니다. 외부 전달 허용으로 표시되지 않습니다.":
        "This is a local download. It is not marked as cleared for external sharing.",
    "대상 유형은 materials/experts/works 중 하나입니다.":
        "The subject type must be one of materials/experts/works.",
    "목적은 training/evaluation 목록입니다.":
        "The purpose must be a list of training/evaluation.",
    "확인 근거와 확인자를 입력하세요.":
        "Enter the confirmation basis and the confirmer.",
    "취소 대상 유형과 ID를 지정하세요.":
        "Specify the type and ID of the target to cancel.",
    "취소할 대상 버전(정수)을 지정하세요.":
        "Specify the version (integer) of the target to cancel.",
    "사유·근거·담당자를 모두 입력하세요.":
        "Enter the reason, basis and owner.",
    "회사 이름을 1~120자로 입력하세요.":
        "Enter a company name of 1-120 characters.",
    "목적은 학습(training) 또는 평가(evaluation)입니다.":
        "The purpose must be training or evaluation.",
    "검증된 작업을 1개 이상 선택하세요.":
        "Select at least one verified work.",
    "카탈로그를 선택하세요.":
        "Select a catalog.",
    "필수 프로젝트를 선택하세요.":
        "Select a required project.",
    "회사를 찾을 수 없습니다.":
        "Company not found.",
    "회사 패키지를 찾을 수 없습니다.":
        "Company package not found.",
    "{}는 {}자 이하 문자열이어야 합니다.":
        "{} must be a string of {} characters or fewer.",
    "프로젝트와 카탈로그 관계가 일치하지 않습니다.":
        "The project and catalog relationship does not match.",
    "작업을 찾을 수 없습니다: {}":
        "Work not found: {}",
    "작업과 카탈로그 관계가 없습니다: {}":
        "The work is not related to the catalog: {}",
    "사람 검증(승인)되지 않은 작업입니다: {}":
        "The work is not human-verified (approved): {}",
    "이 버전의 승인이 취소되었습니다.":
        "Approval of this version has been revoked.",
    "작업 제한은 '{}'입니다.":
        "The work is restricted to '{}'.",
    "과거 승인 취소(사건 {}): {}":
        "Past approval revocation (event {}): {}",
    "가상 과제: {}":
        "Virtual task: {}",
    "기준에 맞게 판단 순서와 근거를 설명":
        "Explain the judgement order and evidence against the criteria",
    "운영 책임자":
        "Operations owner",
    "지정 운영책임자(가상)":
        "Designated operations owner (virtual)",
    "현재 권리·검토 조건으로 추가 내보내기가 보류되었습니다.":
        "Further export is on hold because of the current rights and review conditions.",
    "과제를 찾을 수 없습니다.":
        "Task not found.",
    "권리 대상을 찾을 수 없습니다.":
        "Rights subject not found.",
    "권리 보완: {}":
        "Rights supplement: {}",
    "취소 대상을 찾을 수 없습니다.":
        "Revocation target not found.",
    "승인되지 않은 전문가의 작업입니다: {}":
        "The work is by an unapproved expert: {}",
    "현재 검토 상태가 승인이 아닙니다.":
        "The current review status is not approved.",
    "전문가 승인 상태를 현재 확인할 수 없습니다.":
        "The expert's approval status cannot be confirmed right now.",
    "합성 검토 출력(가상)":
        "Synthetic review output (virtual)",
    "패키지에 없는 과제입니다.":
        "This task is not in the package.",
    "관계·검증 조건을 먼저 보완하세요.":
        "Complete the relationship and verification conditions first.",
    "권리·검토 조건이 부족해 회사를 만들지 않았습니다.":
        "Rights or review conditions are insufficient, so the company was not created.",
    "권리·검토 조건이 부족해 AI 초안을 만들지 않았습니다.":
        "Rights or review conditions are insufficient, so the AI draft was not created.",
    "AI가 만든 초안입니다. 실제 모집 연락을 보낸 것이 아니고 전문가 승인도 아닙니다. 수정한 뒤 사람이 직접 사용하세요.":
        "This is an AI-made draft. No real recruiting message was sent and it is not an expert approval. Edit it and then use it yourself.",
    "도제 경험":
        "Apprenticeship experience",
    "관측 단서":
        "Observation cues",
    "판단/예외":
        "Judgement / exceptions",
    "결과/근거":
        "Result / evidence",
    "승인":
        "Approved",
    "보완":
        "Needs supplement",
    "반려":
        "Rejected",
    "모집문 초안":
        "Recruiting post draft",
    "경험 정리 초안":
        "Experience summary draft",
    "평가 질문 초안":
        "Evaluation question drafts",
    "현재 버전 번호가 필요합니다.":
        "The current version number is required.",
    "4축 점수 객체가 필요합니다.":
        "A four-axis score object is required.",
    "허용되지 않은 점수 항목: {}. 합계는 서버가 계산합니다.":
        "Score fields not allowed: {}. The server calculates the total.",
    "경험 또는 제공 범위가 바뀌었습니다. 현재 평가와 연결된 작업을 다시 확인한 뒤 평가를 기록하세요.":
        "The experience or scope of provision changed. Re-check the work linked to the current evaluation, then record the evaluation.",
    "현재 판정을 확인하세요.":
        "Check the current decision.",
    "합계는 서버가 계산합니다. 보내지 마세요.":
        "The server calculates the total. Do not send it.",
    "판정은 approved(승인), supplement(보완), rejected(반려) 중 하나여야 합니다.":
        "The decision must be one of approved, supplement, rejected.",
    "근거 참조는 200자 이하 문자열 목록(최대 20개)이어야 합니다.":
        "Evidence references must be a list of strings of 200 characters or fewer (up to 20).",
    "업로드 단계에서는 카탈로그를 연결할 수 없습니다. S4에서 나중에 연결하세요.":
        "A catalog cannot be linked at the upload stage. Link it later in S4.",
    "자료 ID 목록(최대 20개)이어야 합니다.":
        "Must be a list of material IDs (up to 20).",
    "비어 있지 않은 문자열 목록이어야 합니다(각 300자 이하).":
        "Must be a non-empty list of strings (300 characters or fewer each).",
    "{} 점수를 입력해 주세요. 빈 값은 0점과 다릅니다.":
        "Enter the {} score. A blank is not the same as 0.",
    "{} 점수는 0~3 정수여야 합니다.":
        "The {} score must be an integer from 0 to 3.",
    "수정할 수 없는 항목입니다: {}":
        "This field cannot be edited: {}",
    "AI 초안 기능이 꺼져 있습니다. 모집문과 질문은 직접 작성하세요.":
        "AI drafts are turned off. Write the recruiting post and questions by hand.",
    "전문가 후보를 찾을 수 없습니다.":
        "Expert candidate not found.",
    "4축 점수와 판정(승인/보완/반려)을 지정 운영책임자가 기록하세요. 점수만으로는 승인되지 않습니다.":
        "The designated operations owner records the four-axis scores and the decision (approve / supplement / reject). Scores alone do not approve.",
    "보완 사유를 확인하고 필요한 경험·근거를 보완한 뒤 다시 평가하세요.":
        "Check the supplement reason, add the needed experience and evidence, then evaluate again.",
    "승인된 전문가입니다. 원시 작업을 업로드할 수 있습니다.":
        "This is an approved expert. Raw work can be uploaded.",
    "반려 사유를 확인하세요. 필요하면 다시 평가를 기록할 수 있습니다. 원시 작업은 업로드할 수 없습니다.":
        "Check the rejection reason. You can record a new evaluation if needed. Raw work cannot be uploaded.",
    "같은 요청 번호로 다른 내용이 이미 저장되었습니다. 새로 작성해 주세요.":
        "Different content was already saved under the same request number. Please write it again.",
    "카탈로그가 아직 없어 미연결(uploaded_unlinked) 상태로 저장했습니다. S4 카탈로그를 만든 뒤 그 화면에서 이 작업을 연결하세요.":
        "There is no catalog yet, so it was saved as unlinked (uploaded_unlinked). Create an S4 catalog, then link this work from that screen.",
    "지원(후보)":
        "Applied (candidate)",
    "다른 곳에서 먼저 수정되었습니다. 새로고침 후 다시 시도해 주세요.":
        "It was edited elsewhere first. Refresh and try again.",
    "승인된 전문가만 원시 작업을 업로드할 수 있습니다. 먼저 평가에서 승인 판정을 기록하세요.":
        "Only approved experts can upload raw work. Record an approval decision in the evaluation first.",
    "경험·도제 맥락·제공 범위가 바뀌었습니다: {}":
        "Experience, apprenticeship context or scope of provision changed: {}",
    "연결할 자료를 찾을 수 없습니다.":
        "The material to link was not found.",
    "수정할 AI 초안이 없습니다.":
        "There is no AI draft to edit.",
    "자료 없음: {}":
        "Material missing: {}",
    "가정 손익입니다. 기업 AX 0원, 현장 인수 결과, 실제 받은 금액과 다르며 회계·가격 정책이 아닙니다.":
        "These are assumed profit figures. They differ from the 0 KRW company AX cost, on-site handover results and amounts actually received, and are not an accounting or pricing policy.",
    "AI 초안은 범위·교육·도구에 대한 제안일 뿐 실제 현장 설치나 성과가 아닙니다. 사람이 읽고 수정해 채택한 제안이며 단계 기록·현장 결과와 별개입니다.":
        "An AI draft is only a proposal for scope, training and tools, not an actual on-site installation or result. It is a proposal a person read, edited and adopted, and is separate from phase records and site results.",
    "계획":
        "Plan",
    "진행":
        "Progress",
    "검토":
        "Review",
    "현장 결과":
        "On-site result",
    "계획값":
        "Planned value",
    "합성 현장":
        "Synthetic site",
    "사람이 기록한 실제 결과":
        "Actual result recorded by a person",
    "ID 목록이어야 합니다.":
        "Must be a list of IDs.",
    "{}개 이하로 선택해 주세요.":
        "Select {} or fewer.",
    "1개 이상 선택해 주세요.":
        "Select at least one.",
    "기업 AX 비용은 0원으로 고정됩니다. 유료 값은 받지 않습니다.":
        "The company AX cost is fixed at 0 KRW. Paid values are not accepted.",
    "가정 손익 (가정값)":
        "Assumed profit (assumed values)",
    "0 이상의 십진 문자열로 보내 주세요(예: \"12000\", \"12000.00\").":
        "Send a decimal string of 0 or more (e.g. \"12000\", \"12000.00\").",
    "이 통화는 소수 {}자리까지 받습니다.":
        "This currency accepts up to {} decimal places.",
    "금액이 허용 범위를 넘습니다.":
        "The amount is out of the allowed range.",
    "기업 AX 비용 0원 (무료 AX, 고정)":
        "Company AX cost 0 KRW (free AX, fixed)",
    "계획·합성 현장·사람이 기록한 실제 결과는 구분해 표시합니다. 기록은 사람이 남긴 값이며 시스템이 실제 설치나 납품을 수행했다는 뜻이 아닙니다.":
        "Plan, synthetic site and actual results recorded by a person are shown separately. Records are values entered by people and do not mean the system actually performed an installation or delivery.",
    "기업용 개선/인수 결과는 구매자 납품·검수와 분리해 기록합니다.":
        "Company improvement / handover results are recorded separately from buyer delivery and acceptance.",
    "구매자 납품·검수는 기업용 개선 결과와 별도로 기록합니다.":
        "Buyer delivery and acceptance are recorded separately from company improvement results.",
    "현재는 합성 프로젝트만 등록할 수 있습니다(true).":
        "Only synthetic projects can be registered for now (true).",
    "양의 정수여야 합니다.":
        "Must be a positive integer.",
    "plan, progress, review, site_result 중 하나여야 합니다.":
        "Must be one of plan, progress, review, site_result.",
    "이 단계에 허용되지 않은 근거 구분입니다.":
        "This evidence type is not allowed for this phase.",
    "수행 근거를 입력해 주세요.":
        "Please enter the basis for what was done.",
    "계획 내용을 입력해 주세요.":
        "Please enter the plan content.",
    "범위·교육·도구 중 하나 이상 남겨 주세요.":
        "Keep at least one of scope, training and tools.",
    "YYYY-MM-DD 형식으로 입력해 주세요.":
        "Please enter it as YYYY-MM-DD.",
    "계산 불가":
        "Not calculable",
    "선택 F6.5 손익 값은 이 화면에서 받지 않습니다.":
        "Optional F6.5 profit values are not accepted on this screen.",
    "허용되지 않은 항목이 있습니다.":
        "Some fields are not allowed.",
    "수정할 수 없는 항목이 있습니다.":
        "Some fields cannot be edited.",
    "expected_version을 보내 주세요.":
        "Please send expected_version.",
    "기업 AX 비용은 0원 고정이며 손익 가정과 분리됩니다.":
        "The company AX cost is fixed at 0 KRW and kept separate from profit assumptions.",
    "요청이 너무 큽니다.":
        "The request is too large.",
    "종료일이 시작일보다 빠를 수 없습니다.":
        "The end date cannot be earlier than the start date.",
    "존재하는 날짜를 입력해 주세요.":
        "Please enter a date that exists.",
    "프로젝트를 찾을 수 없습니다.":
        "Project not found.",
    "카탈로그를 찾을 수 없습니다.":
        "Catalog not found.",
    "지원 통화: {} (환산하지 않습니다).":
        "Supported currencies: {} (not converted).",
    "프로젝트: {}":
        "Project: {}",
    "기업(가상): {}":
        "Company (virtual): {}",
    "과제 카드: {}":
        "Task card: {}",
    "목표: {}":
        "Objective: {}",
    "현재 범위: {}":
        "Current scope: {}",
    "산출물: {}":
        "Deliverables: {}",
    "받지 않는 항목입니다.":
        "This field is not accepted.",
    "작업 {}이(가) 이 카탈로그와 연결돼 있지 않습니다.":
        "Work {} is not linked to this catalog.",
    "전문가를 찾을 수 없습니다: {}":
        "Expert not found: {}",
    "연결 관계를 확인해 주세요.":
        "Please check the linked relationships.",
    "수정할 수 없는 항목입니다.":
        "This field cannot be edited.",
    "단계 기록이 가득 찼습니다.":
        "The phase records are full.",
    "손익 가정에 넣을 수 없습니다.":
        "It cannot be included in the profit assumption.",
    "다른 변경이 먼저 저장됐습니다. 새로 불러와 주세요.":
        "Another change was saved first. Please reload.",
    "샘플 검토":
        "Sample review",
    "유료 파일럿 조건":
        "Paid pilot terms",
    "필요 주제":
        "Topic needed",
    "자료":
        "Material",
    "전문가":
        "Expert",
    "작업(원시·정제)":
        "Work (raw / refined)",
    "카탈로그":
        "Catalog",
    "FDE 프로젝트":
        "FDE project",
    "가상회사":
        "Virtual company",
    "권리":
        "Rights",
    "구매자 의견":
        "Buyer feedback",
    "연결된 보완 기록을 확인하세요.":
        "Check the linked supplement record.",
    "연결 목록은 20개 이하의 배열이어야 합니다.":
        "The link list must be an array of 20 or fewer.",
    "연결 대상(자료·카탈로그·작업·FDE·회사)을 지정하세요.":
        "Specify the link target (material, catalog, work, FDE or company).",
    "처리 상태를 기록하세요.":
        "Record the processing status.",
    "샘플 검토·유료 파일럿 조건·필요 주제 중 하나를 선택하세요.":
        "Choose one of sample review, paid pilot terms or topic needed.",
    "현재는 합성 의견만 저장할 수 있습니다(true).":
        "Only synthetic feedback can be saved for now (true).",
    "연결은 {type, id} 형식이어야 합니다.":
        "A link must be in the form {type, id}.",
    "연결할 수 없는 기록 유형입니다.":
        "This record type cannot be linked.",
    "기록 ID 형식이 올바르지 않습니다.":
        "The record ID format is not valid.",
    "없는 기록입니다: {} {}":
        "No such record: {} {}",
    "알 수 없는 의견 유형입니다.":
        "Unknown feedback type.",
    "알 수 없는 기록 유형입니다.":
        "Unknown record type.",
    "유형 오류":
        "Type error",
    "의견을 찾을 수 없습니다.":
        "Feedback not found.",
    "다른 곳에서 먼저 저장되었습니다. 다시 열어 확인해 주세요.":
        "It was saved elsewhere first. Reopen it and check.",
    "기록을 찾을 수 없습니다.":
        "Record not found.",
    "맥락":
        "Context",
    "관찰 사실":
        "Observed facts",
    "초보자 오류":
        "Novice errors",
    "판단·예외":
        "Judgement / exceptions",
    "결과 확인":
        "Result check",
    "평가 기준":
        "Evaluation criteria",
    "추가 질문":
        "Follow-up questions",
    "AI 초안":
        "AI draft",
    "목표 능력 미지정":
        "Target capability not specified",
    "분야 미지정":
        "Domain not specified",
    "원문에 관찰 문장이 없습니다. 자료를 보완하세요.":
        "The original text has no observation sentences. Please add material.",
    "{} ({}) 과제의 '{}' 작업입니다. 목표 능력: {}.":
        "Work '{2}' under the task '{0}' ({1}). Target capability: {3}.",
    "관찰 사실(원문 발췌): {}":
        "Observed facts (excerpt from original): {}",
    "[추가 확인 필요] 초보자가 자주 하는 오류는 전문가 설명 없이 추정하지 않습니다.":
        "[Needs confirmation] Common novice errors are not guessed without an expert's explanation.",
    "[추가 확인 필요] 판단 기준과 예외는 전문가가 채워야 합니다.":
        "[Needs confirmation] The expert must fill in the judgement criteria and exceptions.",
    "[추가 확인 필요] 결과를 확인하는 방법을 원문 근거와 함께 적으세요.":
        "[Needs confirmation] Write how the result is checked, together with the evidence from the original.",
    "[초안] '{}'를 원문 관찰 근거와 대조해 확인합니다.":
        "[Draft] Check '{}' against the observation evidence in the original.",
    "1. 원문에서 빠진 관찰 단서가 있나요?\n2. 예외 상황은 무엇인가요?":
        "1. Are there observation cues missing from the original?\\n2. What are the exceptional situations?",
    "{} 값은 문자열이어야 합니다.":
        "{} must be a string.",
    "{} 값을 입력해 주세요.":
        "Please enter a value for {}.",
    "{} 값이 너무 깁니다.":
        "The value of {} is too long.",
    "expected_version을 정수로 보내 주세요.":
        "Please send expected_version as an integer.",
    "작업을 찾을 수 없습니다.":
        "Work not found.",
    "승인 전문가":
        "Approved expert",
    "S3 전문가 평가로 이동":
        "Go to S3 expert evaluation",
    "S1 자료·원문으로 이동":
        "Go to S1 materials / original text",
    "카탈로그 연결":
        "Catalog link",
    "S4 카탈로그 연결로 이동":
        "Go to S4 catalog link",
    "정제 전 조건이 충족되지 않았습니다.":
        "The conditions before refinement are not met.",
    "AI 초안 (채택 전에는 참고용)":
        "AI draft (reference only before adoption)",
    "AI 정제 초안이 꺼져 있습니다. 규칙 초안과 직접 교정을 사용하세요.":
        "AI refinement drafts are turned off. Use the rule-based draft and manual correction.",
    "refined 객체를 보내 주세요.":
        "Please send a refined object.",
    "교정 내용을 한 항목 이상 입력해 주세요.":
        "Enter at least one corrected item.",
    "decision은 approved 또는 rejected여야 합니다.":
        "decision must be approved or rejected.",
    "input_hash를 보내 주세요.":
        "Please send input_hash.",
    "evidence_refs는 문자열 목록이어야 합니다.":
        "evidence_refs must be a list of strings.",
    "바꿀 원문(title 또는 body)을 보내 주세요.":
        "Send the original text to replace (title or body).",
    "작업 원문과 연결된 자료 원문이 모두 비어 있습니다.":
        "Both the work original and the linked material original are empty.",
    "카탈로그에 연결되지 않았습니다.":
        "Not linked to a catalog.",
    "사람 작성 원문":
        "Original written by a person",
    "초안":
        "Draft",
    "규칙/수동 생성 초안 (AI 아님)":
        "Rule-based / manual draft (not AI)",
    "교정본":
        "Corrected version",
    "사람 교정":
        "Human correction",
    "body 값이 올바르지 않습니다.":
        "The body value is not valid.",
    "연결된 전문가가 없습니다.":
        "No expert is linked.",
    "연결된 전문가의 평가가 아직 승인되지 않았습니다.":
        "The linked expert's evaluation is not approved yet.",
    "작업이 바뀌었습니다. 새로고침 후 다시 시도하세요.":
        "The work changed. Refresh and try again.",
    "채택 대기 중인 AI 초안이 없습니다.":
        "There is no AI draft awaiting adoption.",
    "카탈로그 미연결":
        "Not linked to a catalog",
    "AI 초안을 먼저 채택해야 교정의 바탕으로 쓸 수 있습니다.":
        "Adopt the AI draft first before using it as the basis for a correction.",
    "지정 운영책임자가 없습니다. 먼저 운영책임자를 지정하세요.":
        "There is no designated operations owner. Designate one first.",
    "지정 운영책임자만 승인·반려를 기록할 수 있습니다.":
        "Only the designated operations owner can record approval or rejection.",
    "작업이 바뀌었습니다. 새로고침 후 다시 확인하세요.":
        "The work changed. Refresh and check again.",
    "검토 대상 입력이 바뀌었습니다. 현재 입력을 다시 확인하세요.":
        "The review input changed. Check the current input again.",
    "교정본이 없어 판정할 수 없습니다.":
        "There is no corrected version, so a decision cannot be made.",
    "이 입력에 같은 판정이 이미 기록되어 있습니다.":
        "The same decision is already recorded for this input.",
    "바뀐 내용이 없습니다.":
        "Nothing has changed.",
    "materials, experts, works 중 하나여야 합니다.":
        "Must be one of materials, experts, works.",
    "대상 ID를 입력해 주세요.":
        "Please enter the subject ID.",
    "권리자를 입력해 주세요(120자 이내).":
        "Please enter the rights holder (within 120 characters).",
    "목적을 선택해 주세요(training, evaluation).":
        "Please choose a purpose (training, evaluation).",
    "confirmed, restricted, unconfirmed, revoked 중 하나여야 합니다.":
        "Must be one of confirmed, restricted, unconfirmed, revoked.",
    "사유를 입력해 주세요.":
        "Please enter the reason.",
    "근거는 {}자 이내 문자열이어야 합니다.":
        "The evidence must be a string within {} characters.",
    "확인·제한 상태에는 근거가 필요합니다.":
        "Evidence is required for confirmed and restricted status.",
    "제한은 {}자 이내 문자열이어야 합니다.":
        "The restrictions must be a string within {} characters.",
    "제한 확인에는 제한 내용을 입력해 주세요.":
        "Please enter the restrictions for a restricted confirmation.",
    "담당자(지정 운영책임자)를 입력해 주세요.":
        "Please enter the owner (designated operations owner).",
    "권리: {} (개정 {})":
        "Rights: {} (revision {})",
    "대상 개정 번호는 1 이상의 정수여야 합니다.":
        "The subject revision number must be an integer of 1 or more.",
    "목적을 하나 이상 선택해 주세요.":
        "Please choose at least one purpose.",
    "unknown, allowed, denied 중 하나여야 합니다.":
        "Must be one of unknown, allowed, denied.",
    "기간 종료일은 YYYY-MM-DD 형식이어야 합니다.":
        "The period end date must be in YYYY-MM-DD format.",
    "대상 기록을 찾을 수 없습니다.":
        "Subject record not found.",
    "대상 개정 번호를 찾을 수 없습니다.":
        "Subject revision number not found.",
    "현재 내용(H)에 대한 권리 확인 기록이 없습니다.":
        "There is no rights confirmation record for the current content (H).",
    "subject_type이 올바르지 않습니다.":
        "subject_type is not valid.",
    "subject_type과 subject_id가 필요합니다.":
        "subject_type and subject_id are required.",
    "purpose는 training 또는 evaluation입니다.":
        "purpose must be training or evaluation.",
    "expected_version(정수)이 필요합니다.":
        "expected_version (integer) is required.",
    "변경 사유와 담당자를 입력해 주세요.":
        "Please enter the reason for the change and the owner.",
    "목적은 training 또는 evaluation만 가능합니다.":
        "The purpose can only be training or evaluation.",
    "'{}': 권리 미확인 상태입니다.":
        "'{}': rights are unconfirmed.",
    "목적 '{}' 이용이 확인되지 않았습니다.":
        "Use for purpose '{}' is not confirmed.",
    "권리 기록을 찾을 수 없습니다.":
        "Rights record not found.",
    "다른 곳에서 먼저 바뀌었어요. 다시 읽고 비교한 뒤 저장해 주세요.":
        "It changed elsewhere first. Read it again, compare, then save.",
    "존재하는 날짜여야 합니다.":
        "Must be a date that exists.",
    "기간 종료일이 지났습니다. 새 기간으로 다시 확인해 주세요.":
        "The period end date has passed. Confirm again with a new period.",
    "'{}': 권리가 철회됐습니다.":
        "'{}': the rights were revoked.",
    "담당자를 입력해 주세요.":
        "Please enter the owner.",
    "대상은 바꿀 수 없습니다. 새 권리 기록을 만드세요.":
        "The subject cannot be changed. Create a new rights record.",
    "다른 곳에서 먼저 바뀌었어요. 다시 읽어 주세요.":
        "It changed elsewhere first. Please read it again.",
    "'{}': 이용 기간이 끝났습니다.":
        "'{}': the usage period has ended.",
    "'{}': 이전 형식 기록이라 현재 내용에 고정되지 않았습니다.":
        "'{}': this is a legacy-format record and is not pinned to the current content.",
    "변경할 수 없습니다.":
        "It cannot be changed.",
    "{}: {}~{} 사이여야 합니다.":
        "{}: must be between {} and {}.",
    "{}: {}자 이하여야 합니다.":
        "{}: must be {} characters or fewer.",
    "제목은 1~{}자 문자열이어야 합니다.":
        "The title must be a string of 1 to {} characters.",
    "본문은 1~{}자 문자열이어야 합니다.":
        "The body must be a string of 1 to {} characters.",
    "{}: 정수여야 합니다.":
        "{}: must be an integer.",
    "공개 시연 설정이 완료되지 않아 요청을 받지 않습니다.":
        "The public demo is not fully configured, so requests are not accepted.",
    "운영자 인증이 필요합니다.":
        "Operator authentication is required.",
    "요청을 확인하지 못했습니다.":
        "The request could not be verified.",
    "공개 시연 설정이 완료되지 않았습니다.":
        "The public demo is not fully configured.",
    "인증에 실패했습니다.":
        "Authentication failed.",
    "공개 시연이 종료되었습니다.":
        "The public demo has ended.",
    "방문자 세션이 없거나 만료되었습니다. 새로고침하세요.":
        "Your visitor session is missing or has expired. Refresh the page.",
}

_compile()
