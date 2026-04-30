# Progress — eTL Calendar Sync

**마지막 갱신:** 2026-04-30

> Cursor 워크플로는 `memory-bank/activeContext.md`(현재 맥락), `tasks.md`(체크리스트), 본 파일(상세 로그)을 함께 쓰도록 `.cursor/commands/`에 정의되어 있음. 이전에는 `memory-bank/`에 파일이 없어, 여기부터 기록이 쌓임.

## 작업 도구 이력
- **~2026-04-17**: Claude Code (터미널)로 구현 작업
- **2026-04-17~25**: 시험 기간 — 작업 중단
- **2026-04-26~**: Claude Cowork(브레인스토밍·설계 논의) + 터미널 Cursor Agent(실제 구현) 병행

## 최근 완료

### `test_gemini.py`
- `classify_exam_announcement` 반환값을 **6-튜플**에 맞게 수정:  
  `(is_exam, exam_date, exam_location, exam_time, has_deadline, deadline_date)`
- 비교는 `is_exam` 기준, 출력에 날짜·시간·장소·마감(`dl=`) 요약 표시

### iCal 동기화 / Google 캘린더 (`sync_runner`, `calendar_service`, `models`, `config`)
- `User`: `exam_color_id`, `assign_color_id` (nullable), 기존 `ical_token` 유지
- `Settings.anthropic_api_key` — Claude 분류기용 (env `ANTHROPIC_API_KEY` 등)
- `init_db()`: SQLite/Postgres 마이그레이션에 색상 컬럼 보강
- `_ical_merge_only`: iCal 항목에 시험 색/과제 색 적용; `exam` 타입에 `classify_exam_announcement` + try/except; 추출 필드가 있을 때만 마감·장소 갱신
- `add_assignment_to_calendar`: `color_id` 반영, `exam_location` 있으면 설명 앞에 `장소:`

### 공개 iCal 피드 (이미 구현됨)
- `GET /api/calendar/{ical_token}/calendar.ics` — `SyncLog` 기반 ICS
- `POST /api/me/ical-token` — 토큰 발급·구독 URL
- 프로덕션에서 구독 URL 고정: **`PUBLIC_BASE_URL=https://<배포호스트>`** (Render 대시보드 Public URL, 끝 슬래시 없음)

### `moodle_ics.py` 이벤트 분류기 (2026-04-29)
- `classify_activity_type(summary, description)` 함수 추가
  - 분류 우선순위: `notice` → `exam` → `presentation` → `assign` → `ical_feed`
  - `notice` 타입은 기본적으로 동기화 제외 (`include_notices=False`)
  - 기존 코드 타입명과 일치: `"assign"`, `"exam"` (canvas_sync, client_sync와 동일 키)
- `get_color_id_for_type(activity_type)` 함수 추가 (Google 캘린더 색상 ID 매핑)
- `ical_to_assignment_items()`에 `activity_type` 분류 + `color_id` 필드 추가
- 배경: 출결 현황·좌석 안내 같은 공지가 시험 이벤트로 잘못 분류되던 문제 수정

### Google OAuth 브랜딩 인증 + 앱 검증 심사 (2026-04-29)
- `static/index.html` `<title>` → `eTL Calendar Sync` 수정 (OAuth 앱 이름 일치)
- `main.py`에 `/privacy-policy` 추가 (기존 `/privacy` 유지), `/terms`, `/google6613324f44353041.html` 복원
- 자동 동기화 구조 확인: cron-job.org → `POST /api/sync/auto-trigger` 10분 주기 (apscheduler 불필요 확인)
- Google Cloud Console 데이터 액세스에 `https://www.googleapis.com/auth/calendar` 범위 등록
- 인증 센터 → 범위 근거, 데모 동영상(YouTube unlisted), 추가 정보 작성 후 심사 제출 완료
- 심사 결과 수주 소요 예상 — 그 전까지 "고급 → 계속"으로 이용 가능

### `moodle_ics.py` 과거 일정 필터링 (2026-04-29)
- `_dtstart_date_kst()` 함수 추가: DTSTART를 Asia/Seoul 기준 날짜로 변환
- `ical_to_assignment_items()`에서 `start_date_kst < today_kst` 이벤트 제외
- datetime/date 타입 모두 처리, DTSTART 없는 이벤트도 제외
- 커밋: 1650d38

### 배포 (2026-04-29)
- Neon DB: `ical_token`, `exam_color_id`, `assign_color_id` ALTER TABLE 실행 완료
- Render 환경변수: `PUBLIC_BASE_URL=https://etl-calendar-sync.onrender.com` 추가
- git push 완료 — main branch: a0e0834 → 15b8106

## 환경 변수 메모
- `ANTHROPIC_API_KEY` — 시험 공지 분류·날짜 추출 (없으면 키워드 fallback)
- `PUBLIC_BASE_URL` — iCal 구독 링크·OAuth 등에 쓸 공개 베이스 URL

## 미진행 / 별도 프로젝트로 보임
다음 항목은 **Next.js 등 다른 코드베이스** 기준이었고, 본 저장소(`etl-calendar`)에서는 작업하지 않음:
- `/api/extract/route.ts` — 과제 카드 묶음, 연도 없는 날짜 파싱
- `DashboardClient.tsx` — 채팅창 높이
- 카드 UI — 원형 게이지 제거, 서브태스크 토글

해당 작업이 필요하면 프로젝트 경로를 지정해 주면 이어서 반영 가능.

## 참고
- GitHub: `jounggyu0319/etl-calender` (리포 이름 오탈자 주의)
- FastAPI 루트에서 실행: `uvicorn app.main:app --reload`
