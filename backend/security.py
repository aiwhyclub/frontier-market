"""Public demo mode (EPIC 13.2): request gate, operator session, visitor session, CSRF.

Everything here runs in a before_request hook, before any route code touches the database.
Only active when FM_PUBLIC_MODE=1; local use is unchanged.

Roles
- visitor: anonymous. May read `/`, `/static/*`, GET `/api/demo/*`, and POST run/reset under
  `/api/demo/`. Holds a server-issued signed cookie (`fm_demo`); mutations also need its CSRF token.
- operator: holds the signed `fm_op` cookie issued by POST /api/auth/login (OPERATOR_TOKEN env).
  Every other route, GETs included, needs it; mutations also need the session-bound CSRF token.

The clock is injected only through create_app(clock=...) (tests). Nothing in a request, header,
cookie or query string can move it.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from flask import Blueprint, current_app, g, jsonify, make_response, request
from itsdangerous import BadSignature, URLSafeTimedSerializer

DEFAULT_END_UTC = "2026-10-24T12:00:00Z"  # 2026-10-24 21:00 Asia/Seoul
OPERATOR_COOKIE = "fm_op"
VISITOR_COOKIE = "fm_demo"
CSRF_HEADER = "X-CSRF-Token"
PUBLIC_PREFIX = "/api/demo/"
AUTH_PREFIX = "/api/auth/"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
MIN_SECRET_LEN = 32
MIN_OPERATOR_TOKEN_LEN = 24

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def parse_utc(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def utc_now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Settings:
    public: bool
    secret: str
    operator_token: str
    end: datetime | None
    cookie_secure: bool
    operator_ttl: int
    visitor_ttl: int
    operator_end: object = None

    @classmethod
    def from_env(cls, env):
        def get(name, default=""):
            return (env.get(name) or default).strip()

        public = get("FM_PUBLIC_MODE") == "1"
        end_raw = get("FM_PUBLIC_END")
        try:
            end = parse_utc(end_raw) if end_raw else None
        except ValueError:
            end = None
        return cls(
            public=public, secret=get("FM_SECRET_KEY"), operator_token=get("OPERATOR_TOKEN"),
            end=end, cookie_secure=get("FM_COOKIE_SECURE", "1") != "0",
            operator_ttl=int(get("FM_OPERATOR_TTL", "28800") or 28800),
            visitor_ttl=int(get("FM_VISITOR_TTL", "21600") or 21600),
            operator_end=_parse_optional(get("FM_OPERATOR_END")))

    def config_problems(self):
        """Public mode fails closed: any missing or weak setting makes every request 503."""
        problems = []
        if len(self.secret) < MIN_SECRET_LEN:
            problems.append("FM_SECRET_KEY")
        if len(self.operator_token) < MIN_OPERATOR_TOKEN_LEN:
            problems.append("OPERATOR_TOKEN")
        if self.end is None:
            problems.append("FM_PUBLIC_END")
        return problems


def _parse_optional(text):
    try:
        return parse_utc(text) if text else None
    except ValueError:
        return None


def settings():
    return current_app.config["FM_SETTINGS"]


def now():
    return current_app.config["FM_CLOCK"]()


def is_closed():
    return now() >= settings().end


def _serializer(purpose):
    return URLSafeTimedSerializer(settings().secret, salt=f"fm-{purpose}-v1")


def _mac(purpose, value):
    return hmac.new(settings().secret.encode(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def _epoch(dt):
    return int(dt.timestamp())


def issue(purpose, ttl):
    """Return (cookie_value, payload). Expiry is stored in the signed payload and checked against
    the injected clock (not the real one), and never extends past the public end."""
    expires = now() + timedelta(seconds=ttl)
    if purpose == "visitor" and settings().end is not None:
        expires = min(expires, settings().end)
    payload = {"p": purpose, "n": secrets.token_hex(16), "exp": _epoch(expires)}
    return _serializer(purpose).dumps(payload), payload


def verify(purpose, cookie):
    """Return the payload for a genuine, unexpired cookie of this purpose, else None."""
    if not cookie:
        return None
    try:
        payload = _serializer(purpose).loads(cookie)
    except BadSignature:
        return None
    if not isinstance(payload, dict) or payload.get("p") != purpose:
        return None
    if not isinstance(payload.get("exp"), int) or _epoch(now()) >= payload["exp"]:
        return None
    if not isinstance(payload.get("n"), str):
        return None
    return payload


def csrf_token(purpose, payload):
    return _mac(f"csrf-{purpose}", payload["n"])


def visitor_ref(payload):
    """Server-derived per-visitor key for the DB (demo_session_ref); never the raw nonce."""
    return _mac("visitor-ref", payload["n"])[:32]


def set_cookie(response, name, value, ttl):
    response.set_cookie(name, value, max_age=ttl, httponly=True, samesite="Strict",
                        secure=settings().cookie_secure, path="/")


def _same_origin():
    origin = request.headers.get("Origin")
    if origin is None:
        return request.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")
    return urlsplit(origin).netloc == request.host


def _csrf_ok(purpose, payload):
    sent = request.headers.get(CSRF_HEADER, "")
    return _same_origin() and hmac.compare_digest(sent, csrf_token(purpose, payload))


def error(status, code, message):
    return jsonify(error={"code": code, "message": message}), status


def is_public_path(path):
    return path == "/" or path.startswith("/static/") or path.startswith(PUBLIC_PREFIX) \
        or path == PUBLIC_PREFIX.rstrip("/")


def gate():
    """before_request: decide role and refuse before any route (and any DB access) runs."""
    cfg = settings()
    if not cfg.public:
        return None
    if cfg.config_problems():
        return error(503, "misconfigured", "공개 시연 설정이 완료되지 않아 요청을 받지 않습니다.")
    if cfg.operator_end is not None and now() >= cfg.operator_end:
        # Hard stop: operator token is retired too, so every route (login included) is refused.
        return error(410, "closed", "공개 시연과 운영자 접근이 모두 종료되었습니다.")
    path = request.path
    closed = is_closed()
    g.role = None
    g.visitor = None

    operator = verify("operator", request.cookies.get(OPERATOR_COOKIE))

    if path.startswith(AUTH_PREFIX) or path == AUTH_PREFIX.rstrip("/"):
        g.operator = operator
        return None  # login/logout/session check themselves; they touch no records

    if is_public_path(path):
        if path.startswith(PUBLIC_PREFIX) or path == PUBLIC_PREFIX.rstrip("/"):
            if closed:
                return error(410, "closed", "공개 시연이 종료되었습니다.")
            visitor = verify("visitor", request.cookies.get(VISITOR_COOKIE))
            if request.method in MUTATING:
                if visitor is None:
                    return error(401, "session_invalid", "방문자 세션이 없거나 만료되었습니다. 새로고침하세요.")
                if not _csrf_ok("visitor", visitor):
                    return error(403, "csrf", "요청을 확인하지 못했습니다.")
            g.role, g.visitor = "visitor", visitor
            return None
        # "/" and static files: public until the end; operators keep the UI afterwards.
        if closed and operator is None:
            return error(410, "closed", "공개 시연이 종료되었습니다.")
        g.role = "operator" if operator else "visitor"
        return None

    # Everything else (GETs included) is operator-only.
    if operator is None:
        return error(401, "operator_required", "운영자 인증이 필요합니다.")
    if request.method in MUTATING and not _csrf_ok("operator", operator):
        return error(403, "csrf", "요청을 확인하지 못했습니다.")
    g.role, g.operator = "operator", operator
    return None


def add_headers(response):
    if settings().public:
        response.headers["Cache-Control"] = "no-store"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


@bp.post("/login")
def login():
    cfg = settings()
    if cfg.config_problems():
        return error(503, "misconfigured", "공개 시연 설정이 완료되지 않았습니다.")
    body = request.get_json(silent=True) or {}
    token = body.get("token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not hmac.compare_digest(
            hashlib.sha256(token.encode()).digest(), hashlib.sha256(cfg.operator_token.encode()).digest()):
        return error(401, "bad_token", "인증에 실패했습니다.")
    if not _same_origin():
        return error(403, "csrf", "요청을 확인하지 못했습니다.")
    value, payload = issue("operator", cfg.operator_ttl)
    response = make_response(jsonify(ok=True, csrf_token=csrf_token("operator", payload),
                                     expires_at=payload["exp"]))
    set_cookie(response, OPERATOR_COOKIE, value, cfg.operator_ttl)
    return response


@bp.post("/logout")
def logout():
    operator = verify("operator", request.cookies.get(OPERATOR_COOKIE))
    if operator is None or not _csrf_ok("operator", operator):
        return error(403, "csrf", "요청을 확인하지 못했습니다.")
    response = make_response(jsonify(ok=True))
    response.delete_cookie(OPERATOR_COOKIE, path="/")
    return response


@bp.get("/session")
def session_info():
    operator = verify("operator", request.cookies.get(OPERATOR_COOKIE))
    if operator is None:
        return error(401, "operator_required", "운영자 인증이 필요합니다.")
    return jsonify(role="operator", csrf_token=csrf_token("operator", operator), expires_at=operator["exp"],
                   closed=is_closed())
