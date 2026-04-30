# Tasks — eTL Calendar Sync

**마지막 갱신:** 2026-04-30

## 완료 (최근)
- [x] `test_gemini.py`: `classify_exam_announcement` 6-튜플 언팩·출력 정리
- [x] User `exam_color_id` / `assign_color_id`, `Settings.anthropic_api_key`, DB 마이그레이션
- [x] `_ical_merge_only` 색상·Claude 분류·fallback 동작
- [x] `add_assignment_to_calendar`에 `color_id`, `exam_location` 반영
- [x] 공개 iCal: `POST /api/me/ical-token`, `GET /api/calendar/{token}/calendar.ics` (기존 구현 유지)
- [x] `moodle_ics.py` 이벤트 분류기 추가 (classify_activity_type, notice 필터링, color_id, include_notices=False)
- [x] Neon DB 마이그레이션 실행 (ical_token, exam_color_id, assign_color_id ALTER TABLE)
- [x] Render `PUBLIC_BASE_URL` 환경변수 추가 및 재배포 완료
- [x] git push (main branch: a0e0834 → 15b8106)
- [x] Google OAuth 브랜딩 인증 완료 (`<title>`, `/privacy-policy`, `/privacy`, `/terms`, `/google...html` 수정·복원)
- [x] Google Cloud Console 데이터 액세스에 `auth/calendar` 범위 등록
- [x] Google OAuth 앱 검증 심사 제출 완료 (2026-04-29) — 결과 대기 중
- [x] `moodle_ics.py` 과거 일정 필터링 추가 (오늘 이전 DTSTART 이벤트 제외, KST 기준)

## 진행 예정
- [ ] Google OAuth 앱 검증 심사 결과 확인 (수주 소요 예상)
- [ ] 다른 캘린더 추가 UI (구독 URL 발급 버튼 + 안내 문구) — Apple/Outlook 대상
- [ ] 로그인 전 에러 배너 수정 (프론트 이슈 — 비로그인 시 /api/me/ 401을 에러로 표시)

## 장기 / 방향성 논의 필요
- [ ] 다른 학교 Canvas 지원 (myetl.snu.ac.kr 하드코딩 제거, 학교별 도메인 입력 구조)
- [ ] 스터디맵 연동 (eTL 수집 데이터 → 스터디맵 자동 카드 생성, 스터디맵 완성 후)

## 제외 결정
- ~~Canvas 토큰 자동화 (마우스 자동화)~~ — 화면 좌표 의존, 서버 배포 불가, 사용자 범용성 없음

## 보류 / 다른 코드베이스
- [ ] Next.js: `/api/extract/route.ts` 과제 카드 묶음·연도 없는 날짜
- [ ] `DashboardClient.tsx` 채팅 높이
- [ ] 카드 UI: 원형 게이지 제거·서브태스크 토글

## 복잡도
- 위 보류 항목: 별도 프론트/풀스택 프로젝트에서 진행 시 Level 2–3 추정 (경로 확정 후 `tasks.md` 갱신)
