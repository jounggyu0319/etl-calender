# Project Context — eTL Calendar Sync

**규칙: 이 파일은 세션 종료 시 반드시 업데이트하고 git commit. 커밋 안 된 내용은 신뢰 불가.**

---

## 1. 배포 상태 (커밋 기준, Render 실행 중)

**최신 커밋:** `1650d38` (2026-04-29)
**배포 URL:** https://etl-calendar-sync.onrender.com
**DB:** Neon PostgreSQL
**자동 동기화:** cron-job.org → `POST /api/sync/auto-trigger` (X-Cron-Secret 헤더 인증)

### 구현 완료 (커밋됨)

**모델 (app/models.py)**
- `User`: `canvas_token_enc`, `assign_color_id`, `exam_color_id`, `auto_sync_enabled`, `auto_sync_interval_hours`, `last_auto_sync_at`
- `SyncLog`: 동기화 항목 로그 (최근 200건 유지)

**엔드포인트**
| 경로 | 기능 |
|------|------|
| `POST /api/sync/` | iCal 간편 동기화 (구독 URL → Google) |
| `POST /api/sync/canvas` | Canvas API 전체 동기화 (과제·퀴즈·시험 공지) |
| `POST /api/sync/auto-trigger` | cron-job.org 트리거 (헤더 인증) |
| `GET /api/sync/history` | 동기화 내역 조회 (SyncLog) |
| `GET /api/sync/progress` | 동기화 진행 폴링 |
| `POST /api/sync/etl/prepare` | Selenium 브라우저 준비 (로컬 only) |
| `POST /api/sync/etl/continue` | Selenium 동기화 실행 (로컬 only) |
| `POST /api/me/ical-token` | 개인 iCal 구독 토큰 발급 |
| `PATCH /api/me/canvas-token` | Canvas API 토큰 저장 |
| `PATCH /api/me/auto-sync` | 자동 동기화 on/off |
| `PATCH /api/me/color-settings` | 캘린더 색상 저장 |
| `GET /api/me/check-connections` | 연결 상태 확인 |
| `GET /api/calendar/{token}/calendar.ics` | 공개 iCal 피드 |

**핵심 서비스 파일 (커밋됨)**
- `app/services/canvas_sync.py` — Canvas API 수집·Google 반영
- `app/services/auto_sync.py` — 전체 유저 iCal 자동 동기화
- `app/services/gemini_classifier.py` — 시험 공지 분류 (Claude/키워드 fallback)
- `app/snu_academic_calendar.py` — 학기 필터 윈도우 계산
- `calendar_service.py` (루트) — Google Calendar API 래퍼

**외부 연동**
- Google OAuth 앱 검증 심사 제출 완료 (2026-04-29), 결과 대기 중 (수주 소요)
- `ANTHROPIC_API_KEY` 환경변수: 시험 공지 분류용 (없으면 키워드 fallback)
- `CRON_SECRET` 환경변수: auto-trigger 인증용

---

## 2. 로컬 WIP (미커밋, 배포 안 됨)

> ⚠️ 이 섹션의 내용은 git에 없음. 로컬 파일과 커밋 파일이 다를 때는 `git show HEAD:파일경로`로 커밋 버전을 확인할 것.

**새로 추가된 파일 (??)**
- `app/services/moodle_ics.py` — iCal 이벤트 분류기, 과거 일정 필터, notice 제외
- `app/services/ical_feed.py` — SyncLog → iCal 변환 (공개 구독 피드용)
- `app/routers/calendar.py` — `GET /api/calendar/{token}/calendar.ics` 엔드포인트
- `memory-bank/` 전체 — 미추적

**로컬에서 삭제된 파일 (D, 커밋엔 존재)**
- `app/services/canvas_sync.py` — 커밋엔 있음, 로컬 삭제 (리팩토링 중으로 보임)
- `app/services/auto_sync.py` — 커밋엔 있음, 로컬 삭제
- `app/snu_academic_calendar.py` — 커밋엔 있음, 로컬 삭제

**수정 중인 파일 (M)**
- `app/models.py`, `app/schemas.py`, `app/serializers.py`
- `app/routers/me.py`, `app/routers/sync.py`
- `app/services/sync_runner.py`, `app/services/calendar_service.py`
- `calendar_service.py` (루트), `requirements.txt`

**미커밋 상태 정리**
- 새 iCal 구독 기능(moodle_ics, ical_feed, calendar router)은 준비됐지만 아직 커밋 안 됨
- 커밋된 canvas_sync/auto_sync와 로컬 수정본 사이의 호환성 미검증
- 커밋 전 로컬 수정 파일들의 diff 검토 필요

---

## 3. 다음 할 것

- [ ] 로컬 WIP diff 검토 후 커밋 전략 정리 (새 iCal 기능 커밋 vs 기존 파일 정리)
- [ ] Google OAuth 심사 결과 확인 (대기 중)
- [ ] iCal 구독 UI — 구독 URL 발급 버튼 + Apple/Outlook 안내 (프론트)
- [ ] 로그인 전 에러 배너 수정 (만료 토큰 → 배너 없이 조용히 로그아웃)
- [ ] memory-bank/tasks.md 와 이 파일을 커밋

---

## 4. 핵심 결정 사항

| 결정 | 이유 |
|------|------|
| APScheduler 제거 → cron-job.org | Render 무료 플랜에서 스케줄러 유지 어려움 |
| Selenium 프로덕션 비활성화 | 서버 환경에서 브라우저 실행 불가 |
| Canvas API 토큰 방식 채택 | 사용자가 myetl 프로필에서 직접 발급 가능 |
| iCal 구독 URL 방식 병행 | Google OAuth 인증 없이도 사용 가능한 경로 |
| notice 이벤트 기본 제외 | 출결·안내 이벤트가 캘린더를 오염시켜서 |

---

## 5. Claude Cowork 세션 규칙

- **세션 시작 시**: `git status` 먼저 실행해서 로컬 vs 커밋 차이 확인
- **파일 읽을 때**: `M`/`D` 표시 파일은 `git show HEAD:파일경로`로 커밋 버전 확인
- **세션 종료 시**: 이 파일(context.md) + tasks.md 업데이트 후 커밋
