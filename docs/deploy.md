# 배포 결정서 — EPIC 13 해커톤 공개 시연 (STORY 13.1)

상태: 결정과 로컬 준비(13.2~13.3 코드·테스트)까지 완료. **가입·결제·push·실제 배포는 하지 않았고 사용자 승인이 필요합니다.** 실제 Postgres에 대한 실행은 검증하지 못했습니다(아래 8절). 근거: `docs/plan.md` EPIC 13, `docs/trd.md` §5.2·§8·§9.2·§11.1.

## 1. 결정 요약

| 항목 | 결정 |
|---|---|
| 호스팅 | Vercel(Python 런타임, Flask WSGI 앱). 진입점 `api/index.py`, 모든 경로를 `vercel.json` rewrite로 이 함수에 보냄 |
| 영구 DB | Neon Postgres 무료 구간. `DATABASE_URL`이 있으면 Postgres, 없으면 SQLite(로컬 기본) |
| 운영자 인증 | 계정 추가 없음. `OPERATOR_TOKEN` 환경변수 1개 → 로그인 시 서명·만료·nonce 쿠키 + CSRF 토큰 |
| 방문자 세션 | 서버가 발급하는 서명 쿠키(`fm_demo`), 암호학적 난수 nonce, 서버가 서명·만료·용도 검증 |
| 종료 | 공개 종료 `2026-10-24T12:00:00Z`(21:00 KST). 앱이 모든 요청 처음에 서버 시계로 검사, DB 접근보다 먼저 410 |
| 종료 후 | 공개 경로(`/api/demo/*`, `/`, 정적 파일) 410. 운영자 쿠키가 있으면 운영 API와 화면은 계속 사용. DB는 보존 |
| 비용 | 소프트웨어 사용료 $0(Vercel Hobby + Neon Free). 실제 AI 호출 0회(C-06 A) |

종료 시각이 이미 지났다면(작성 시점 2026-10-24) TRD §5.2에 따라 **공개 배포를 하지 않고** 비공개 검토 경로(로컬 SQLite 또는 운영자 쿠키가 필요한 비공개 배포)만 사용합니다.

## 2. 권한

| 구분 | 허용 | 경로 |
|---|---|---|
| 공개 방문자(익명) | 합성 자료 조회, 본인 세션의 과제 run/reset | `GET /`, `GET /static/*`, `GET /api/demo/*`, `POST /api/demo/companies/{id}/run`, `POST /api/demo/companies/{id}/reset` |
| 운영자(`OPERATOR_TOKEN` 보유) | 등록·수정·권리 확인·승인/취소·회사 생성·export 등 나머지 전부 | `/api/records`, `/api/company/*`, `/api/rights`, `/api/health` 등 **GET 포함 `/api/demo` 밖의 모든 경로** |

원칙: 허용 목록(`/`, `/static/`, `/api/demo/`, 로그인) 밖은 존재 여부와 상관없이 운영자 세션이 없으면 401이며 DB·핸들러에 도달하지 않습니다. 경로별 인증 누락 GET이 공개로 열리는 일이 없습니다. 공개 경로에는 등록·수정·권리·승인·회사 생성·export가 없습니다.

공개 응답은 `synthetic=true` 레코드만, 화면용 필드(id/type/title/status/body/version/hash)만 내며 `fields_json`, `input_hash`, 감사 기록, DB 경로, 키는 내지 않습니다. 회사 상세는 작업 답안·문서 본문을 포함하지 않고, 현재 권리/HOLD 검사(`company._export_gate`)를 매 요청에 다시 하며 HOLD면 `hold: true`로 표시하고 run/reset은 409로 막습니다. 과거 receipt 재요청도 HOLD 검사를 먼저 통과해야 합니다.

## 3. 운영자 인증 (계정 추가 없이)

1. 운영자는 `POST /api/auth/login {"token": OPERATOR_TOKEN}`. 상수 시간 비교.
2. 성공하면 쿠키 `fm_op` 발급: `itsdangerous` 서명(`FM_SECRET_KEY`, 용도 salt `fm-operator-v1`), 페이로드 `{p, n(nonce 128bit), exp}`. `HttpOnly`, `SameSite=Strict`, `Secure`, `Path=/`. 기본 8시간(`FM_OPERATOR_TTL`).
3. 응답의 `csrf_token`은 `HMAC(secret, "csrf-operator:" + nonce)`로 세션에 결합됩니다. 모든 변경 요청(POST/PUT/PATCH/DELETE)은 헤더 `X-CSRF-Token`이 같고 `Origin`이 있으면 요청 호스트와 같아야 합니다. 아니면 403.
4. 만료·위조·다른 용도(방문자 쿠키)의 쿠키는 거절합니다. 만료는 서명 페이로드의 `exp`를 서버 시계로 확인합니다.
5. 토큰 교체 = 환경변수 교체 후 재배포. `FM_SECRET_KEY`를 바꾸면 모든 세션이 무효화됩니다(긴급 로그아웃 수단).

한계: 로그인 시도 횟수 제한이 없습니다. 토큰은 24자 이상(권장 `openssl rand -hex 32`)으로 강제하고, 필요하면 Vercel Firewall rate limit을 추가합니다(미확인).

## 4. 방문자 세션과 분리

- `GET /api/demo/bootstrap`이 유효한 쿠키가 없으면 새 `fm_demo` 쿠키와 CSRF 토큰을 발급합니다. 조회(GET)는 쿠키가 위조·만료여도 새 세션을 받아 계속 읽을 수 있지만, **run/reset은 유효한 세션과 CSRF가 없으면 401/403**입니다.
- DB 키 `demo_session_ref`는 `HMAC(secret, "visitor-ref:" + nonce)`의 앞 32자로 서버가 계산합니다. 요청 본문의 session/scope/ref 값은 읽지 않습니다.
- 방문자 시도는 `demo_runs`/`demo_run_history`/`demo_receipts` 테이블에만 저장합니다. 운영자 `company_runs`는 공개 요청이 읽지도 쓰지도 않습니다. (TRD §3은 company_runs에 컬럼을 추가하는 형태지만, 다른 에이전트가 company 기능을 수정 중이므로 별도 테이블로 같은 의미를 구현했습니다. 필요하면 후속으로 합칠 수 있습니다.)
- `run_version`: 미생성 조회 0, 최초 run/reset 저장 1, 이후 저장마다 +1(reset 포함, 되돌리지 않음). `(회사, 과제, 방문자)`당 현재 시도 1개는 부분 고유 인덱스로 보장합니다. `expected_run_version` 불일치는 409.
- receipt 범위 `request_scope = "demo:" + demo_session_ref`는 서버가 계산합니다. 같은 범위·같은 `Idempotency-Key`·같은 본문은 저장된 결과를 `replayed: true`로 반환하고 다시 쓰지 않으며, 본문이 다르면 409입니다. 다른 방문자의 receipt는 반환하지 않습니다.
- 남용 방지: 방문자 세션당 기록 300건 상한(429).

## 5. 종료 (서버 측 차단)

- 검사 위치: Flask `before_request` 첫 훅. DB 조회·쓰기·receipt 반환보다 앞입니다. 종료 후 `/api/demo/*`, `/`, `/static/*`는 운영자 쿠키가 없으면 410, `/api/demo/*`는 운영자도 410(공개 면 자체를 닫음).
- 기존 방문자 쿠키는 종료 시각 검사에서 먼저 막혀 후속 요청이 모두 거절됩니다. 쿠키 만료도 종료 시각을 넘지 않게 발급합니다.
- 시계는 `create_app(clock=...)`로 테스트에서만 주입합니다. 헤더·쿼리·쿠키·본문으로 바꿀 수 없습니다(테스트로 확인). 운영 환경은 서버 UTC 시계를 씁니다.
- 설정 누락 시 닫힌 채로 실패: `FM_SECRET_KEY`(32자 이상), `OPERATOR_TOKEN`(24자 이상), `FM_PUBLIC_END`가 없거나 약하면 모든 요청 503.
- 모든 응답에 `Cache-Control: no-store`(앱 + `vercel.json` headers). Service Worker는 사용하지 않으며 등록 코드가 없습니다. 이미 받은 화면·파일의 회수는 보장하지 않습니다(TRD 명시).
- 앱 방어와 별도로 2차 수단(권장, 사용자 승인 필요): 종료 시각 직후 Vercel에서 `FM_PUBLIC_MODE`는 그대로 두고 deployment protection(Vercel Authentication)을 켜거나 도메인 별칭을 제거. 앱의 410이 1차, 호스팅 차단이 2차입니다.

## 6. 호스팅·DB 구성

환경변수(모두 Vercel Project Settings, 저장소에 넣지 않음):

| 이름 | 값 |
|---|---|
| `DATABASE_URL` | Neon **pooled** 연결 문자열(`...-pooler...`, `sslmode=require`) |
| `FM_PUBLIC_MODE` | `1` |
| `FM_SECRET_KEY` | 난수 32바이트 이상(`openssl rand -hex 32`) |
| `OPERATOR_TOKEN` | 난수 24자 이상(`openssl rand -hex 24`) |
| `FM_PUBLIC_END` | `2026-10-24T12:00:00Z` |
| (선택) `FM_OPERATOR_TTL`, `FM_VISITOR_TTL` | 초 단위, 기본 28800 / 21600 |

`FM_COOKIE_SECURE`는 기본 켜짐(Vercel은 HTTPS)이며 로컬 http 시험에서만 `0`을 씁니다. AI 키 변수는 설정하지 않습니다.

- DB 분리: 공개 시연은 **새 Neon 프로젝트**(합성 데이터 전용)를 씁니다. 로컬 운영·검수 DB 자료를 넣지 않습니다. 첫 요청(콜드 스타트)이 스키마·합성 시드를 만들며 Postgres advisory lock으로 동시 콜드 스타트를 직렬화합니다.
- DB 계층: `backend/dbcompat.py`의 얇은 래퍼가 `?`→`%s`, `INSERT OR IGNORE`, `AUTOINCREMENT`, `sqlite_version()`, `json_extract`, `LIKE`→`ILIKE`, PRAGMA, `BEGIN IMMEDIATE`를 변환하고 행을 `row[0]`/`row["name"]` 둘 다 지원합니다. 기능 모듈은 변경하지 않았습니다. 드라이버 오류는 `sqlite3` 예외로 바꿔 기존 `except sqlite3.Error`가 동작합니다.
- 전용 WSGI: Vercel Python 런타임이 `api/index.py`의 `app`을 WSGI로 호출합니다. Flask 개발 서버는 배포 경로에 없습니다. `server.py`는 로컬 전용으로 유지합니다.
- TLS: Vercel이 제공. 별도 도메인 구매는 하지 않고 `*.vercel.app`을 씁니다.

## 7. 비용·한도 (조회 없이 작성한 개략치 — 가입 전 공식 페이지로 재확인)

| 서비스 | 플랜 | 비용 | 알려진 한도(재확인 필요) |
|---|---|---|---|
| Vercel | Hobby | $0 | 비상업 용도 약관, 함수 실행 시간·호출·전송량 한도. 한도 초과 시 일시 중단(자동 과금 없음으로 알고 있음) |
| Neon | Free | $0 | 프로젝트당 저장소 약 0.5GB, 월 컴퓨트 시간 한도, 유휴 시 scale-to-zero(첫 요청 지연 수 초) |
| AI | 사용 안 함 | $0 | C-06 A |

합성 자료와 짧은 방문 기간이라 한도 초과 가능성은 낮지만, 가입 시 결제 수단 요구 여부와 초과 정책을 확인한 뒤에만 진행합니다. 결제 수단 등록이 필요하면 진행하지 않고 사용자에게 돌려줍니다.

## 8. 검증 상태

- 확인됨(로컬, SQLite): 앱 전체 테스트, 공개 모드 경계(20:59:59/21:00:00/21:00:01 KST), 방문자 A/B·운영자 격리, 위조·만료 세션·CSRF 거절, 인증 없는 운영 API(GET 포함) 거절, 설정 누락 시 503, no-store.
- 확인됨(단위): SQL 변환 규칙, 행 객체, 래퍼의 BEGIN/COMMIT/ROLLBACK·lastval·오류 변환(가짜 드라이버). 전체 테스트가 실행하는 모든 SQL 문(122종)이 변환 후 `?`·AUTOINCREMENT·sqlite 전용 구문이 남지 않음을 확인했습니다.
- **미확인: 실제 Postgres 실행.** 이 장비에 Postgres가 없고 Neon에 가입하지 않았습니다. 첫 배포 전 Neon 일회용 브랜치를 `TEST_DATABASE_URL`로 지정해 `python -m unittest backend.test_dbcompat`과 공개 모드 스모크를 돌려야 합니다. 예상 위험: 문자열 정렬 규칙·NULL 정렬 차이, 다른 기능이 나중에 추가하는 sqlite 전용 SQL, 스키마를 요청마다 `CREATE TABLE IF NOT EXISTS`로 확인하는 기능(company)의 DDL 부하, Vercel Python 런타임의 Python 3.14 지원 여부.
- 백업/복원: `backend/backup.py`와 `scripts/db_backup.py`는 SQLite 전용입니다. Neon에서는 Neon의 시점 복구/브랜치(무료 플랜은 복구 기간이 짧음)와 `pg_dump` 수동 내보내기를 씁니다. 종료 직후 `pg_dump`를 운영자 로컬에 보관하는 것을 권장합니다. 복원 리허설은 아직 하지 않았습니다.

## 9. 종료 후 비공개 전환

1. 앱이 이미 공개 면을 410으로 막습니다(5절).
2. 운영자는 `/api/auth/login`으로 로그인한 쿠키로 `/`와 운영 API를 계속 사용합니다.
3. 데이터 보존: DB·개정본·snapshot·이력은 삭제하지 않습니다. 최종 `pg_dump`를 받은 뒤 필요하면 Vercel 프로젝트를 일시 중지하거나 삭제하고, Neon 프로젝트는 보존 여부를 사용자가 결정합니다.

## 10. 미해결 항목 (사용자 결정·승인 필요)

1. Vercel·Neon 가입과 프로젝트 생성, 결제 수단 요구 여부 확인.
2. 환경변수 값 생성·주입(비밀값은 사용자가 보관). 
3. 실제 배포(`vercel deploy`)와 push — 별도 지시 전에는 하지 않습니다.
4. 실제 Postgres 스모크와 백업 복원 리허설.
5. Python 3.14의 Vercel 지원 확인(미지원이면 `requires-python`/`.python-version`으로 3.13 사용 가능한지 검증 필요).
6. 공개 화면(static)은 아직 `/api/demo/*`만 호출하는 방문자용 UI로 바뀌지 않았습니다. 현재 `/`의 JS는 운영자 API를 호출하므로 공개 방문자에게는 401로 보입니다. 방문자 전용 화면과 지정 5폭 검수는 후속 작업입니다.
7. 종료 후 실제 공유 URL·캐시/프록시 경로 확인은 실제 배포가 있어야 가능합니다(13.3의 실공개 항목).
8. 로그인 시도 제한(Vercel Firewall) 적용 여부.
9. 종료 시각(2026-10-24T12:00:00Z)이 이미 지났다면 공개 배포를 생략합니다.
