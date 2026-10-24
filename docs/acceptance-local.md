# 로컬 필수 범위 수용 검수 결과 (STORY 11.6)

- 대상: 현재 코드(`main` 기준 작업 트리, 커밋 전)와 PRD §7.1 AC-01~AC-13의 로컬 필수 범위
- 기준 문서: [PRD §7.1](prd.md), [Plan EPIC 11](plan.md)
- 검수 시각: 2026-10-24 (로컬 개발 서버 `127.0.0.1:8787`, 합성 시드 DB)
- 실행 명령: `.venv/bin/python -m unittest discover -s backend -t .` → **181 tests, OK** (4.2초)

## 상태 표기

| 상태 | 뜻 |
|---|---|
| 통과(실행함) | 이 검수에서 기존 테스트를 실행했거나 GET 요청을 실제로 보내 확인함 |
| 미실행 | 필요한 확인(브라우저 뷰포트·키보드·동시 요청 등)을 이번에 수행하지 않음 |
| 차단 | 실행 조건(브라우저 자동화, 키, 외부 환경)이 없어 수행 불가 |
| EPIC13 이관 | 공개 방문자·공개 권한·21시 종료 등 EPIC 13에서 최종 대조할 항목 |

"통과(실행함)"은 해당 테스트 파일이 전체 181 테스트에 포함되어 OK로 끝났다는 뜻이며, 개별 테스트명을 적은 행은 그 이름을 근거로 한다. 파일 단위로만 적은 행은 파일 안의 전체 테스트가 통과했다는 뜻이다.

## 1. 화면·단계·의견·무료 AX·게이트 대조

| 항목 | 근거 (test / file:line / GET) | 상태 |
|---|---|---|
| PRD 8개 화면 (S0 대시보드, S1~S7) 존재 | `static/index.html:25-32` 내비에 `dashboard`, `s1`~`s7` 마커 존재 (파일 확인). `GET /` → 200 | 통과(실행함, GET·파일 확인). 화면별 조작은 미실행 |
| 화면 S2 DB 목록·요약 | `GET /api/records/summary` → counts(catalog 1, companies 1, experts 2, feedback 1, materials 3, projects 1, rights 3, works 2). `test_records.test_summary_counts_every_type_with_seed` | 통과(실행함) |
| 1단계 자료 수집 | `backend/features/test_collect.py`(파일 전체 통과); `test_records.test_material_post_returns_201_and_detail_matches` | 통과(실행함) |
| 2단계 DB 저장·복원 | `test_records.test_restart_restores_same_id_and_content_without_duplicating_seed`, `test_backup.test_backup_mutate_restore_returns_original_rows_versions_and_links` | 통과(실행함) |
| 3단계 전문가 평가·승인·업로드 | `backend/features/test_experts.py`(파일 전체 통과); `GET /api/experts` 405(POST 전용, 정상 라우팅) | 통과(실행함, 단위) |
| 4단계 카탈로그 생성·연결 | `backend/features/test_catalog.py`(파일 전체 통과); `GET /api/catalog/` → `cat_seed01` | 통과(실행함, 단위) |
| 5단계 정제·검증 | `test_refine.test_rule_draft_is_not_a_review_and_keeps_input_hash`, `test_refine.test_validate_requires_designated_operator_and_current_hash`; `GET /api/refine/works` → `wrk_seed02` 상태 `uploaded_unlinked` | 통과(실행함) |
| 6단계 FDE 프로젝트 | `test_fde.test_create_rejects_missing_catalog_and_unlinked_work`, `test_fde.test_stage_records_basis_and_evidence`; `GET /api/fde/projects` → `prj_seed01` | 통과(실행함) |
| 7단계 가상회사 (생성·게이트) | `test_company.test_rights_hold_then_supplement_then_create_and_export`, `test_company.test_missing_project_and_unverified_work_block_without_saving`; `GET /api/company/candidates`, `GET /api/company/companies` → `co_seed01` | 통과(실행함) |
| 구매자 의견 3유형 (sample_review·pilot·topic) | `test_feedback.test_create_each_kind_and_list_by_kind`, `test_feedback.test_topic_saves_without_any_catalog_on_empty_db`, `test_feedback.test_dashboard_shape`; `GET /api/feedback/dashboard` 200 | 통과(실행함) |
| 무료 AX 0원 | `test_fde.test_nonzero_ax_fee_is_rejected_and_zero_accepted`, `test_fde.test_price_and_cost_fields_are_refused`; `GET /api/fde/projects` → `company_ax_fee.amount = 0` | 통과(실행함) |
| 생성 게이트 (권리·검증·목적) | `test_company.test_rights_hold_then_supplement_then_create_and_export`, `test_company.test_evaluation_check_passes_with_training_supplement` | 통과(실행함) |
| 내보내기 게이트 | `test_company.test_revoke_past_approval_holds_export_and_new_creation`; `GET /api/company/companies/co_seed01/export` → 404 `not_found` (시드에는 패키지가 없음) | 통과(단위). 성공 경로 내보내기는 미실행 |
| 권리 seed 값 | `GET /api/rights` → `rgt_seed03` `status: unconfirmed` (미확인 기록은 이용 불가로 표시) | 통과(실행함) |

## 2. AC-01~AC-13 로컬 범위

| AC | PRD 기준 요지 | 근거 | 상태 |
|---|---|---|---|
| AC-01 | URL/텍스트 등록 후 새로고침, 출처·본문 유지, 접근 실패 오류 표시 | `test_collect.py` (SSRF·리다이렉트 차단 포함, 예: `test_collect.py:65`), `test_records.test_material_post_*` | 통과(실행함, 단위). 브라우저 새로고침 확인은 미실행 |
| AC-02 | 미승인 업로드 차단, 카탈로그 없이도 승인 후 등록 | `test_experts.py`, `test_fde.test_create_requires_synthetic_and_at_least_one_work` | 통과(실행함, 단위) |
| AC-03 | 자료·전문가 묶어 카탈로그 생성, DB 저장·연결 표시 | `test_catalog.py`, `GET /api/catalog/` | 통과(실행함, 단위) |
| AC-04 | 검증 승인 전 회사 생성 차단, 정제 방식 표시 | `test_company.test_missing_project_and_unverified_work_block_without_saving`; `refine.py:276` "규칙/수동 생성 초안 (AI 아님)" | 통과(실행함) |
| AC-05 | 계획 단계 프로젝트에서 학습용·평가용 각각 생성, 고정 스냅샷 | `test_company.test_evaluation_check_passes_with_training_supplement`, `test_company.test_rights_hold_then_supplement_then_create_and_export` | 통과(실행함, 단위). 두 목적 UI 흐름은 미실행 |
| AC-06 | 학습용 요청(평가 전용)·권리 미확인·동의 미확인 각각 보류, 서버 차단 | `company.py:120` 평가 전용 보류 문구, `_subject_hold`(`company.py:97`), `test_company.test_rights_hold_then_supplement_then_create_and_export` | 통과(실행함, 단위) |
| AC-07 | 범위·단계·산출물 저장, 기업 AX 0원 유지 | `test_fde.test_patch_rejects_ax_fee_and_unknown_fields`, `test_fde.test_plan_cannot_claim_actual_basis`; `GET /api/fde/projects` | 통과(실행함, 단위) |
| AC-08 (로컬) | 점검 전 출하 거절, 점검→출하→환경 초기화, 과제별 새 시도 | `test_company.test_task_run_release_before_inspection_rejected_and_reset_keeps_history`; `GET /api/company/companies/co_seed01/runs` → 200 `{"runs":[]}` | 통과(실행함, 단위). 로컬 UI 실행 흐름은 미실행. 공개 방문자 A/B 분리는 **EPIC13 이관** |
| AC-09 | 원문 개정 사유·새 버전·이력, 과거 패키지 불변 | `test_refine.test_correction_creates_new_version_and_keeps_original`, `test_refine.test_original_revision_makes_approval_stale_and_keeps_history`, `test_records.test_revisions_keep_every_version`, `test_company.test_revoke_past_approval_holds_export_and_new_creation` | 통과(실행함) |
| AC-10 | 세 유형 후속 기록 저장·조회·수정·빈 DB 신규 topic | `test_feedback.test_edit_status_and_note_then_reread`, `test_feedback.test_topic_saves_without_any_catalog_on_empty_db`, `test_feedback.test_link_to_follow_up_records_and_relink` | 통과(실행함) |
| AC-11 (로컬) | 서버 재시작 후 DB 복원, 5폭 조회, Web/Mobile 동등 행동 | 재시작: `test_records.test_restart_restores_same_id_and_content_without_duplicating_seed`, `test_records.test_repeated_restarts_do_not_duplicate_seed_rows`. 5폭: `backend/test_s2_ui.py` 존재하나 본 검수에서 뷰포트 실행 없음 | 재시작 복원 통과(실행함). **320/375/768/1024/1440px 및 역할별 동등 행동은 미실행**. 공개 권한·21시 차단은 **EPIC13 이관** |
| AC-12 | 키/모델 설정과 무관하게 실제 AI 호출·외부 전송·API 지출 0 | 아래 2-1 참조 | 통과(코드 검사 + 실행함). 키가 설정된 상태의 실행 검증은 미실행 |
| AC-13 | 권리 철회/만료/미확인 또는 검증 취소 후 추가 내보내기 보류, 스냅샷 보존 | `test_rights.test_revoke_holds_and_records_event`, `test_rights.test_expired_grant_is_hold`, `test_rights.test_no_grant_is_hold`, `test_company.test_revoke_past_approval_holds_export_and_new_creation` | 통과(실행함) |

### 2-1. AC-12 코드 경로 검사 (실행한 검사 명령)

- `backend` 전체에서 `openai`, `anthropic`, `api_key`, `llm`, `gpt` 문자열 검색 → 제품 코드(테스트 제외)에서 일치 없음.
- 환경 변수 읽기(`getenv`, `environ`) → 제품 코드 0건. 키를 읽는 경로가 없어 키 설정 여부와 무관하게 호출되지 않는다.
- HTTP 클라이언트 사용은 `backend/features/collect.py:17,135,268`의 `http.client` 한 곳뿐이다. 이는 사용자가 입력한 **자료 URL 가져오기**(S1)이며 AI 호출이나 데이터 외부 전송이 아니다. 사설망·루프백 주소 차단은 `test_collect.py:65` 등으로 확인했다.
- `refine.py:360` "No AI call; a draft is not a review." 및 `company.py:299` `"synthetic-rule"`, "외부 실행 없음" 표시를 확인했다.
- 테스트 `test_feedback.test_mocked_no_network_module_has_no_http_clients`(`backend/features/test_feedback.py:138`)가 피드백 모듈의 HTTP 클라이언트 미사용을 검사하며 통과했다.
- 결론: 현재 로컬 경로에는 실제 AI 호출·AI 외부 전송·API 지출 경로가 없다. 실제 AI 성공·실패·사용량 검수는 향후 별도 도입 시 수행한다(PRD 기준 선택 12.1~12.6).

## 3. 미실행·차단·EPIC13 이관 목록

| 항목 | 상태 | 사유 |
|---|---|---|
| S0~S7 브라우저 조작 (표→카드, 폼, 하단 행동) | 미실행 | 이번 검수는 서버 테스트와 GET만 수행. 브라우저 자동화 미사용 |
| 320/375/768/1024/1440px 가로 넘침·가려짐 검수 (AC-11, STORY 11.2) | 미실행 | 뷰포트 실행 없음 |
| 키보드·포커스·44px·safe-area (STORY 11.3) | 미실행 | 브라우저 필요 |
| 중복 요청 1회 저장·버전 충돌 동시 실행 (STORY 11.4) | 부분 통과 | 버전 충돌은 `test_records.test_stale_version_is_rejected`, `test_feedback.test_stale_version_conflicts_and_keeps_data`, `test_rights.test_stale_expected_version_is_409`로 확인. 동시 중복 요청 시나리오는 미실행 |
| 성공 경로 내보내기 패키지 다운로드 | 미실행 | 시드에 패키지 없음 (404 확인). 생성 후 내보내기는 `test_company` 단위로만 확인 |
| AC-08 공개 방문자 A/B 분리·직접 API 우회 | EPIC13 이관 (13.2~13.3) | 공개 기능은 로컬 검수 범위 밖 |
| AC-11 공개 권한·세션 격리·21:00 KST(12:00 UTC) 종료 차단 | EPIC13 이관 (13.2~13.3) | 공개 종료 시각 2026-10-24 21:00 KST 기준 |
| 실제 AI 호출 성공·실패·사용량 | 해당 없음 (향후 선택) | AI 미도입. PRD 12.1~12.6 |

## 4. 이번 검수에서 바꾼 파일

- `docs/acceptance-local.md` (신규, 이 파일만 작성)
- 제품 코드·테스트·정적 파일은 수정하지 않았다. 커밋하지 않았다.

## 5. 한계

- 본 결과는 소프트웨어 로컬 검수이며 실제 사업 검증(구매 의사, 전문가 모집 성공, 현장 AX 완료)이 아니다.
- 서버 테스트 통과와 GET 응답은 화면 동작을 증명하지 않는다. 화면 항목은 브라우저 실행 전까지 미실행으로 남긴다.
