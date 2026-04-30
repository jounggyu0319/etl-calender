# Cursor 작업 표준 가이드 (bkit)

## 작업 시작 시 (필수)
1. `.bkit/state/pdca-state.json` 읽기
2. `activeFeature` 확인 → 해당 feature의 `tasks` 중 담당 항목 파악
3. 관련 `planDoc` 있으면 읽기

## 작업 완료 시 (필수)
1. `.bkit/state/pdca-state.json` 업데이트:
   - 완료한 task → `tasks` 배열에서 ✅ 표시 또는 제거
   - `status` 갱신 (`planned` → `in_progress` → `done`)
   - `lastUpdated` 날짜 갱신
2. 커밋 메시지에 feature 이름 포함

## 규칙
- push는 절대 하지 말 것 (Claude·사용자가 검수 후 결정)
- 한 커밋에 여러 피처 섞지 말 것
- 완료 후 변경 요약을 간결하게 보고할 것 (파일명·핵심 변경만)
