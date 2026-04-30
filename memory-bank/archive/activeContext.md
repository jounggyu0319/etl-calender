# Active Context — eTL Calendar Sync

**마지막 갱신:** 2026-04-27

## 지금 맥락
- 백엔드: FastAPI + SQLAlchemy, iCal 구독 → Google 동기화, 공개 `calendar.ics` 피드, Claude(Anthropic) 시험 공지 분류.
- 최근 작업: `test_gemini.py`를 6-튜플 API에 맞춤, User 색상 컬럼·`anthropic_api_key`·캘린더 `color_id`/장소 반영 등(상세는 `progress.md`).

## 기록 파일 역할 (이 레포 Cursor 워크플로)
| 파일 | 용도 |
|------|------|
| `tasks.md` | 할 일·체크리스트·복잡도 |
| `activeContext.md` | (이 파일) 현재 초점·한 줄 요약 |
| `progress.md` | 구현·테스트·환경 변수 등 상세 로그 |

`memory-bank/`는 예전에 비어 있었고, 상세 로그는 `progress.md`에 쌓는 중.
