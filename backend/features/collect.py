"""S1 자료 수집 (EPIC 3, F1.1~F1.6).

- 공개 URL 가져오기: http/https, 80/443, 공개 IP만. DNS 결과를 검사한 같은 주소로 연결하고
  리다이렉트마다 다시 검사한다. timeout 10초, 최대 5회 이동, 수신 본문 500KiB, 저장 텍스트 100,000자.
- 본문 직접 등록: URL 없이 같은 materials 기록으로 저장한다.
- 원문·근거 개정: 사유·담당자를 함께 저장하면 새 버전과 collect_events 사건을 남기고,
  이 원문을 참조하는 작업(works --from--> materials)을 재검토 필요로 표시한다.
- 정리 초안: 기본은 규칙 기반. FM_AI_ENABLED 등으로 채택된 경우에만 AI 초안(F10.1)을 시도하고,
  모든 시도는 fields.draft_attempts 에 남기며 실패하면 규칙 초안으로 계속한다. 채택/교정 결과는 fields.organized에 따로 저장하고
  원문(body)은 덮어쓰지 않는다.

Errors: {"error": {"code", "message"[, "fields"|"reason"]}}.
"""

import functools
import hashlib
import ipaddress
import http.client
import json
import re
import socket
import ssl
import sqlite3
from contextlib import closing
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from flask import Blueprint, current_app, jsonify, request

from backend import ai, db, i18n

bp = Blueprint("collect", __name__, url_prefix="/api/collect")

ALLOWED_PORTS = {80, 443}
MAX_REDIRECTS = 5
FETCH_TIMEOUT_SECONDS = 10
MAX_BYTES = 500 * 1024
MAX_TEXT_CHARS = 100_000
MAX_BODY_CHARS = 20_000
MAX_TITLE_CHARS = 120
MAX_META_CHARS = 300
MAX_URL_CHARS = 2000
ALLOWED_TYPES = ("text/html", "text/plain", "application/json")
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
META_KEYS = ("kind", "source_type", "source_name", "source_url", "evidence")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS collect_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        material_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        from_version INTEGER,
        to_version INTEGER,
        reason TEXT,
        owner TEXT,
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS collect_events_material ON collect_events (material_id, id)",
]


def init_schema(conn):
    for statement in SCHEMA:
        conn.execute(statement)


class FetchError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------- public URL fetch

def _resolve(host, port):
    """Return every address the host resolves to. Tests replace this to avoid DNS."""
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _is_public_ip(address):
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_unspecified or ip.is_reserved
    )


def _check_public_url(url):
    """Validate one URL and pin it to a verified public address. Raises FetchError."""
    if len(url) > MAX_URL_CHARS or any(ch.isspace() for ch in url):
        raise FetchError("invalid_url", "주소 형식을 확인해 주세요. 공백 없이 전체 주소를 입력해 주세요.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise FetchError("invalid_url", "주소 형식을 확인해 주세요.")
    if parts.scheme not in ("http", "https"):
        raise FetchError("unsupported_scheme", "http 또는 https 주소만 가져올 수 있습니다.")
    if not parts.hostname or parts.username or parts.password:
        raise FetchError("invalid_url", "주소 형식을 확인해 주세요. 계정 정보가 들어간 주소는 사용할 수 없습니다.")
    port = port or (443 if parts.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise FetchError("blocked_port", "80 또는 443 포트의 공개 주소만 가져올 수 있습니다.")
    host = parts.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise FetchError("blocked_address", "내부 또는 비공개 주소는 수집할 수 없습니다.")
    try:
        ipaddress.ip_address(host)
        addresses = [host]  # an IP literal is checked as written, never through DNS
    except ValueError:
        try:
            addresses = _resolve(host, port)
        except socket.gaierror:
            raise FetchError("dns_error", "주소를 찾지 못했습니다. 주소를 확인하거나 본문을 직접 붙여 넣어 주세요.")
    if not addresses:
        raise FetchError("dns_error", "주소를 찾지 못했습니다. 주소를 확인하거나 본문을 직접 붙여 넣어 주세요.")
    for address in addresses:
        try:
            public = _is_public_ip(address)
        except ValueError:
            public = False
        if not public:
            raise FetchError("blocked_address", "내부 또는 비공개 주소는 수집할 수 없습니다.")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return {"url": url, "scheme": parts.scheme, "host": host, "port": port, "path": path,
            "ip": addresses[0]}


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, pinned_ip, host, port, timeout):
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self):
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


class _PinnedHTTPS(_PinnedHTTP):
    def connect(self):
        super().connect()
        self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)


def _fetch_once(target):
    """One request to the pinned address. Returns (status, lowercase headers, response)."""
    cls = _PinnedHTTPS if target["scheme"] == "https" else _PinnedHTTP
    conn = cls(target["ip"], target["host"], target["port"], FETCH_TIMEOUT_SECONDS)
    conn.request("GET", target["path"], headers={
        "User-Agent": "FrontierMarketCollector/0.1",
        "Accept": "text/html,text/plain,application/json;q=0.9",
        "Accept-Encoding": "identity",
    })
    resp = conn.getresponse()
    headers = {k.lower(): v for k, v in resp.getheaders()}
    return resp.status, headers, resp


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "template"}
    BREAK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title_parts = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skip:
            self.parts.append(data)


def _clean_text(text):
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.splitlines()]
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return cleaned.strip()


def _read_body(target, headers, resp):
    ctype_full = headers.get("content-type", "")
    ctype = ctype_full.split(";")[0].strip().lower()
    if ctype not in ALLOWED_TYPES:
        raise FetchError("unsupported_type",
                         "HTML·텍스트·JSON 자료만 가져올 수 있습니다. PDF·영상·로그인 페이지는 본문을 직접 붙여 넣어 주세요.")
    length = headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_BYTES:
        raise FetchError("too_large", "자료가 500KiB를 넘어 가져오지 않았습니다. 필요한 부분을 본문으로 붙여 넣어 주세요.")
    raw = resp.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise FetchError("too_large", "자료가 500KiB를 넘어 가져오지 않았습니다. 필요한 부분을 본문으로 붙여 넣어 주세요.")
    charset = "utf-8"
    match = re.search(r"charset=([\w.-]+)", ctype_full, re.IGNORECASE)
    if match:
        charset = match.group(1)
    try:
        text = raw.decode(charset)
    except (LookupError, UnicodeDecodeError):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise FetchError("unreadable", "문자 코드를 읽을 수 없습니다. 본문을 직접 붙여 넣어 주세요.")
    title = ""
    if ctype == "text/html":
        parser = _TextExtractor()
        parser.feed(text)
        parser.close()
        title = " ".join("".join(parser.title_parts).split())
        text = _clean_text("".join(parser.parts))
    else:
        text = _clean_text(text)
    if not text:
        raise FetchError("empty", "읽을 수 있는 본문이 없습니다. 본문을 직접 붙여 넣어 주세요.")
    if len(text) > MAX_TEXT_CHARS:
        raise FetchError("text_too_long", "본문이 10만 자를 넘어 가져오지 않았습니다. 일부만 본문으로 붙여 넣어 주세요.")
    return {"text": text, "title": title[:MAX_TITLE_CHARS], "content_type": ctype,
            "domain": target["host"], "fetched_at": db.now_iso()}


def fetch_public_url(url):
    """Fetch a public page. Returns text, title, final_url, fetched_at. Raises FetchError."""
    current = url.strip()
    try:
        for _ in range(MAX_REDIRECTS + 1):
            target = _check_public_url(current)
            status, headers, resp = _fetch_once(target)
            try:
                if status in REDIRECT_STATUSES:
                    location = headers.get("location")
                    if not location:
                        raise FetchError("http_error", "리다이렉트 위치가 없어 가져오지 못했습니다.")
                    current = urljoin(target["url"], location)
                    continue
                if status != 200:
                    raise FetchError("http_error", f"서버가 HTTP {status} 응답을 주어 가져오지 못했습니다.")
                result = _read_body(target, headers, resp)
                result["final_url"] = target["url"]
                return result
            finally:
                resp.close()
        raise FetchError("too_many_redirects", "리다이렉트가 5회를 넘어 가져오지 않았습니다.")
    except FetchError:
        raise
    except TimeoutError:
        raise FetchError("timeout", "10초 안에 응답이 없어 가져오지 못했습니다. 다시 요청하거나 본문을 직접 붙여 넣어 주세요.")
    except (OSError, http.client.HTTPException, ssl.SSLError):
        raise FetchError("network_error", "연결하지 못했습니다. 다시 요청하거나 본문을 직접 붙여 넣어 주세요.")


# ---------------------------------------------------------------- records helpers

class _Invalid(Exception):
    def __init__(self, fields):
        super().__init__("invalid")
        self.fields = fields


class _Stale(Exception):
    pass


def _conn():
    conn = db.connect(current_app.config["DB_PATH"])
    init_schema(conn)
    return conn


def _error(status, code, message, **extra):
    return jsonify(error={"code": code, "message": message, **extra}), status


def _json_body():
    if request.mimetype != "application/json":
        return None, _error(415, "unsupported_media_type", "application/json 으로 보내 주세요.")
    if request.content_length and request.content_length > 1024 * 1024:
        return None, _error(413, "payload_too_large", "요청이 1MiB를 넘었습니다.")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, _error(400, "invalid_json", "JSON 객체를 보내 주세요.")
    return data, None


def _sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _raw_row(conn, record_type, record_id):
    return conn.execute("SELECT * FROM records WHERE type = ? AND id = ?",
                        (record_type, record_id)).fetchone()


def _text_field(data, key, *, required=False, limit=MAX_META_CHARS, errors=None):
    value = data.get(key)
    if value is None or value == "":
        if required:
            errors[key] = "필수 항목입니다."
        return ""
    if not isinstance(value, str) or len(value.strip()) > limit:
        errors[key] = f"{limit}자 이하 문자열이어야 합니다."
        return ""
    value = value.strip()
    if required and not value:
        errors[key] = "필수 항목입니다."
    return value


def _expected_version(data):
    value = data.get("expected_version")
    if type(value) is not int or value < 1:
        raise _Invalid({"expected_version": "정수여야 합니다. 화면을 새로고침한 뒤 다시 저장해 주세요."})
    return value


def _record_event(conn, material_id, kind, *, from_version=None, to_version=None,
                  reason=None, owner=None, detail=None):
    conn.execute(
        "INSERT INTO collect_events (material_id, kind, from_version, to_version, reason, owner, "
        "detail_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (material_id, kind, from_version, to_version, reason, owner,
         db.canonical_json(detail or {}), db.now_iso()),
    )


def _mark_dependent_works(conn, material_id, version, reason):
    """Works that use this material as their source need re-review after a revision."""
    rows = conn.execute(
        "SELECT DISTINCT from_id FROM record_links WHERE from_type = 'works' AND to_type = 'materials' "
        "AND to_id = ? AND relation = 'from' ORDER BY from_id",
        (material_id,),
    ).fetchall()
    marked = []
    for row in rows:
        work_id = row["from_id"]
        work = _raw_row(conn, "works", work_id)
        if work is None:
            continue
        fields = json.loads(work["fields_json"])
        fields.update({
            "review_state": "review_required",
            "review_reason": f"원천 자료 {material_id} 개정 v{version}: {reason}",
            "review_source": {"type": "materials", "id": material_id, "version": version},
        })
        db.update_record(conn, "works", work_id, expected_version=work["version"],
                         changes={"fields": fields})
        marked.append(work_id)
    return marked


def _guard(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except _Invalid as exc:
            return _error(400, "validation_error", "입력을 확인해 주세요.", fields=exc.fields)
        except _Stale as exc:
            return _error(409, "stale_draft", str(exc))
        except db.RecordNotFound:
            return _error(404, "not_found", "자료를 찾을 수 없습니다.")
        except db.RecordConflict:
            return _error(409, "version_conflict",
                          "다른 곳에서 먼저 저장되었습니다. 최신 내용을 다시 읽은 뒤 저장해 주세요.")
        except sqlite3.Error:
            return _error(500, "storage_error", "저장하지 못했습니다. 입력은 그대로 두었으니 다시 시도해 주세요.")
    return wrapper


def _create_material(conn, title, body, fields):
    with db.write_transaction(conn):
        rid = db.create_record(conn, "materials", title=title, status="draft", synthetic=True,
                               body=body, fields=fields)
        return db.get_record(conn, "materials", rid)


# ---------------------------------------------------------------- routes

@bp.post("/fetch")
@_guard
def fetch_material():
    data, err = _json_body()
    if err:
        return err
    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        return _error(400, "validation_error", "가져올 주소를 입력해 주세요.", fields={"url": "필수 항목입니다."})
    try:
        result = fetch_public_url(url)
    except FetchError as exc:
        # The request input is not cleared by the client; the reason explains what to change.
        return jsonify(error={"code": "fetch_failed", "reason": exc.code, "message": exc.message,
                              "input": {"url": url}}), 422
    title = result["title"] or result["domain"]
    fields = {
        "origin": "url",
        "source_url": url.strip(),
        "final_url": result["final_url"],
        "fetched_at": result["fetched_at"],
        "source_domain": result["domain"],
        "content_type": result["content_type"],
        "content_hash": _sha(result["text"]),
        "kind": "", "source_type": "웹 페이지", "source_name": result["domain"], "evidence": "",
    }
    with closing(_conn()) as conn:
        item = _create_material(conn, title[:MAX_TITLE_CHARS], result["text"], fields)
    return jsonify(item=item), 201


@bp.post("/materials")
@_guard
def create_manual_material():
    data, err = _json_body()
    if err:
        return err
    errors = {}
    unknown = set(data) - {"title", "body", "synthetic", *META_KEYS}
    for key in unknown:
        errors[key] = "허용되지 않은 항목입니다."
    title = _text_field(data, "title", required=True, limit=MAX_TITLE_CHARS, errors=errors)
    body = data.get("body")
    if not isinstance(body, str) or not body.strip():
        errors["body"] = "본문을 입력해 주세요."
    elif len(body.strip()) > MAX_BODY_CHARS:
        errors["body"] = f"본문은 {MAX_BODY_CHARS}자 이하여야 합니다."
    if data.get("synthetic", True) is not True:
        errors["synthetic"] = "현재는 합성 자료만 등록할 수 있습니다(true)."
    meta = {key: _text_field(data, key, errors=errors) for key in META_KEYS}
    if errors:
        raise _Invalid(errors)
    fields = {"origin": "manual", "fetched_at": None, "final_url": None,
              "content_hash": _sha(body.strip()), **meta}
    with closing(_conn()) as conn:
        item = _create_material(conn, title, body.strip(), fields)
    return jsonify(item=item), 201


@bp.get("/materials/<record_id>")
@_guard
def get_material(record_id):
    with closing(_conn()) as conn:
        item = db.get_record(conn, "materials", record_id)
        if item is None:
            raise db.RecordNotFound(record_id)
        events = [dict(r) for r in conn.execute(
            "SELECT id, kind, from_version, to_version, reason, owner, detail_json, created_at "
            "FROM collect_events WHERE material_id = ? ORDER BY id DESC", (record_id,)).fetchall()]
        for event in events:
            event["detail"] = json.loads(event.pop("detail_json"))
        revisions = [dict(r) for r in conn.execute(
            "SELECT version, material_hash, created_at FROM record_revisions "
            "WHERE type = 'materials' AND id = ? ORDER BY version DESC", (record_id,)).fetchall()]
        works = [dict(r) for r in conn.execute(
            "SELECT DISTINCT l.from_id AS id, w.title AS title, w.version AS version FROM record_links l "
            "JOIN records w ON w.type = 'works' AND w.id = l.from_id "
            "WHERE l.from_type = 'works' AND l.to_type = 'materials' AND l.to_id = ? AND l.relation = 'from' "
            "ORDER BY l.from_id", (record_id,)).fetchall()]
    organized = item.get("organized")
    organized_stale = bool(organized) and organized.get("based_on_body_hash") != _sha(item.get("body"))
    return jsonify(item=item, events=events, revisions=revisions, dependent_works=works,
                   organized_stale=organized_stale, ai=ai.status("F10.1"))


@bp.get("/materials/<record_id>/revisions/<int:version>")
@_guard
def get_revision(record_id, version):
    with closing(_conn()) as conn:
        row = conn.execute(
            "SELECT snapshot_json, material_hash, created_at FROM record_revisions "
            "WHERE type = 'materials' AND id = ? AND version = ?", (record_id, version)).fetchone()
    if row is None:
        return _error(404, "not_found", "개정본을 찾을 수 없습니다.")
    return jsonify(version=version, snapshot=json.loads(row["snapshot_json"]),
                   material_hash=row["material_hash"], created_at=row["created_at"])


@bp.patch("/materials/<record_id>")
@_guard
def update_material(record_id):
    data, err = _json_body()
    if err:
        return err
    expected = _expected_version(data)
    with closing(_conn()) as conn:
        with db.write_transaction(conn):
            row = _raw_row(conn, "materials", record_id)
            if row is None:
                raise db.RecordNotFound(record_id)
            if row["version"] != expected:
                raise db.RecordConflict(record_id)
            fields = json.loads(row["fields_json"])
            new_fields = dict(fields)
            errors = {}
            changes = {}
            if "title" in data:
                changes["title"] = _text_field(data, "title", required=True, limit=MAX_TITLE_CHARS, errors=errors)
            for key in META_KEYS:
                if key in data:
                    new_fields[key] = _text_field(data, key, errors=errors)
            body_changed = False
            if "body" in data:
                body = data["body"]
                if not isinstance(body, str) or not body.strip():
                    errors["body"] = "원문을 비워 둘 수 없습니다."
                elif len(body.strip()) > MAX_BODY_CHARS:
                    errors["body"] = f"원문은 {MAX_BODY_CHARS}자 이하여야 합니다."
                else:
                    body_changed = body.strip() != (row["body"] or "")
                    changes["body"] = body.strip()
            evidence_changed = "evidence" in data and new_fields.get("evidence") != fields.get("evidence", "")
            revision = body_changed or evidence_changed
            reason = _text_field(data, "reason", required=revision, errors=errors)
            owner = _text_field(data, "owner", required=revision, errors=errors)
            if errors:
                raise _Invalid(errors)
            if body_changed:
                new_fields["content_hash"] = _sha(changes["body"])
            if revision:
                new_fields["revision_count"] = int(fields.get("revision_count", 0)) + 1
                new_fields["last_revision"] = {"reason": reason, "owner": owner, "at": db.now_iso()}
            changes["fields"] = new_fields
            item = db.update_record(conn, "materials", record_id, expected_version=expected, changes=changes)
            if item["version"] == expected:
                return jsonify(item=item, dependent_works=[], changed=False)
            marked = []
            if revision:
                changed = [name for name, flag in (("원문", body_changed), ("근거", evidence_changed)) if flag]
                _record_event(conn, record_id, "revision", from_version=expected,
                              to_version=item["version"], reason=reason, owner=owner,
                              detail={"changed": changed})
                marked = _mark_dependent_works(conn, record_id, item["version"], reason)
    return jsonify(item=item, dependent_works=marked, changed=True)


@bp.post("/materials/<record_id>/draft")
@_guard
def make_draft(record_id):
    data, err = _json_body()
    if err:
        return err
    expected = _expected_version(data)
    with closing(_conn()) as conn:
        with db.write_transaction(conn):
            row = _raw_row(conn, "materials", record_id)
            if row is None:
                raise db.RecordNotFound(record_id)
            if row["version"] != expected:
                raise db.RecordConflict(record_id)
            body = row["body"] or ""
            if not body.strip():
                raise _Invalid({"body": "원문이 없어 초안을 만들 수 없습니다."})
            fields = json.loads(row["fields_json"])
            draft, attempt_run = _make_draft(body)
            fields["draft"] = draft
            if attempt_run:
                fields["draft_attempts"] = (fields.get("draft_attempts") or []) + [attempt_run]
            item = db.update_record(conn, "materials", record_id, expected_version=expected,
                                    changes={"fields": fields})
            _record_event(conn, record_id, "draft", from_version=expected, to_version=item["version"],
                          detail={"mode": draft["mode"], "ai_attempted": bool(attempt_run),
                                  "ai_ok": bool(attempt_run and attempt_run.get("ok"))})
    return jsonify(item=item), 200


AI_SYSTEM = ("너는 수집한 자료를 구조화하는 보조자다. 자료에 있는 내용만 쓰고 추측하지 않는다. "
             'JSON 객체 하나만 답한다: {"summary": 문자열(300자 이하), "key_points": 문자열 배열(최대 5개)}')


def parse_ai_draft(text):
    """Return the rule-draft shape from model output, or None when it is not valid JSON of that shape."""
    if not isinstance(text, str):
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    summary, points = data.get("summary"), data.get("key_points")
    if not isinstance(summary, str) or not summary.strip():
        return None
    if not isinstance(points, list) or not points or not all(isinstance(p, str) and p.strip() for p in points):
        return None
    summary = summary.strip()[:300]
    points = [p.strip()[:200] for p in points[:5]]
    return {"text": "\n".join([i18n.tr("요약: ") + summary] + ["- " + p for p in points]),
            "summary": summary, "key_points": points}


def _make_draft(body):
    """Try the AI draft (F10.1) when adopted; otherwise or on any failure keep the rule draft.

    Returns (draft, attempt) where attempt is None when no real call was made.
    """
    base = {"body_hash": _sha(body), "created_at": db.now_iso()}
    text, run = ai.attempt("F10.1", AI_SYSTEM, body[:MAX_BODY_CHARS])
    if not run.get("attempted"):
        return {**build_rule_draft(body), "mode": "rule_based", "model": None, **base}, None
    parsed = parse_ai_draft(text) if run.get("ok") else None
    if run.get("ok") and parsed is None:
        run = {**run, "ok": False, "error": "unparsable_response", "mode": "rule_based"}
    attempt_run = {k: run.get(k) for k in ("provider", "model", "started_at", "finished_at", "ok",
                                           "error", "usage", "input_hash")}
    attempt_run["adopted"] = False
    if parsed:
        return ({**parsed, "mode": "ai", "model": run["model"], "provider": run["provider"],
                 "started_at": run["started_at"], "finished_at": run["finished_at"],
                 "input_hash": run["input_hash"], "usage": run.get("usage"), **base}, attempt_run)
    draft = {**build_rule_draft(body), "mode": "rule_based", "model": None, **base,
             "ai_failure": {"error": attempt_run["error"], "at": attempt_run["finished_at"]}}
    return draft, attempt_run


def build_rule_draft(body):
    """Rule-based structuring: summary and key sentences only. Nothing is inferred beyond the text."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", body) if s.strip()]
    key_points = [s[:200] for s in sentences[:5]]
    summary = " ".join(sentences[:2])[:300]
    text = "\n".join([i18n.tr("요약: ") + summary] + ["- " + point for point in key_points])
    return {"text": text, "summary": summary, "key_points": key_points}


@bp.post("/materials/<record_id>/organized")
@_guard
def organize(record_id):
    """Explicitly adopt the draft as-is ('adopt') or save a corrected version ('correct')."""
    data, err = _json_body()
    if err:
        return err
    expected = _expected_version(data)
    action = data.get("action")
    errors = {}
    reviewer = _text_field(data, "reviewer", required=True, limit=120, errors=errors)
    if action not in ("adopt", "correct"):
        errors["action"] = "adopt 또는 correct 중 하나여야 합니다."
    notes = _text_field(data, "correction_notes", required=(action == "correct"), limit=MAX_META_CHARS, errors=errors)
    corrected = ""
    if action == "correct":
        corrected = data.get("text")
        if not isinstance(corrected, str) or not corrected.strip():
            errors["text"] = "교정한 정리본을 입력해 주세요."
        elif len(corrected.strip()) > MAX_BODY_CHARS:
            errors["text"] = f"정리본은 {MAX_BODY_CHARS}자 이하여야 합니다."
    if errors:
        raise _Invalid(errors)
    with closing(_conn()) as conn:
        with db.write_transaction(conn):
            row = _raw_row(conn, "materials", record_id)
            if row is None:
                raise db.RecordNotFound(record_id)
            if row["version"] != expected:
                raise db.RecordConflict(record_id)
            fields = json.loads(row["fields_json"])
            draft = fields.get("draft")
            if not draft:
                raise _Invalid({"draft": "먼저 정리 초안을 만들어 주세요."})
            body = row["body"] or ""
            if draft.get("body_hash") != _sha(body):
                raise _Stale("원문이 바뀌어 초안이 오래되었습니다. 초안을 다시 만든 뒤 채택해 주세요.")
            previous = fields.get("organized") or {}
            organized = {
                "text": draft["text"] if action == "adopt" else corrected.strip(),
                "origin": ("ai" if draft.get("mode") == "ai" else "rule") + (
                    "_draft_adopted" if action == "adopt" else "_draft_corrected"),
                "draft_mode": draft.get("mode", "rule_based"),
                "draft_model": draft.get("model"),
                "based_on_body_hash": draft["body_hash"],
                "draft_created_at": draft.get("created_at"),
                "correction_notes": notes,
                "reviewer": reviewer,
                "adopted_at": db.now_iso(),
                "organized_version": int(previous.get("organized_version", 0)) + 1,
            }
            fields["organized"] = organized
            item = db.update_record(conn, "materials", record_id, expected_version=expected,
                                    changes={"fields": fields})
            _record_event(conn, record_id, "organize", from_version=expected, to_version=item["version"],
                          reason=notes or None, owner=reviewer,
                          detail={"action": action, "organized_version": organized["organized_version"]})
    return jsonify(item=item), 200
