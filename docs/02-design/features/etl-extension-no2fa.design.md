# etl-extension-no2fa Design Document

> **Summary**: Chrome Extension MV3 + Electron WebView 기반 2FA-free 동기화 설계
> **Date**: 2026-04-13
> **Status**: Implementation In Progress
> **Plan Doc**: [etl-extension-no2fa.plan.md](../01-plan/features/etl-extension-no2fa.plan.md)

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 2FA로 인한 Selenium 기반 동기화 실사용 불가 |
| **WHO** | SNU 재학생 |
| **RISK** | myetl DOM 구조 변경 |
| **SUCCESS** | 클릭 한 번 → Calendar 일정 생성 |
| **SCOPE** | Extension > Electron > 웹앱 UI 순 |

---

## 1. Extension 아키텍처

### 파일 구조
```
extension/
├── manifest.json          ✅ 완료
├── background.js          🔄 Cursor 작업 중 (PROGRESS 릴레이)
├── content-bridge.js      ✅ 완료 (PING + COLLECT + PROGRESS)
├── popup.html             ✅ 완료
├── popup.js               ✅ 완료
├── lib/
│   └── myetl-collect.js   ✅ 완료
└── icons/
    ├── icon16.png          🔄 Cursor 작업 중
    ├── icon48.png          🔄 Cursor 작업 중
    └── icon128.png         🔄 Cursor 작업 중
```

### 메시지 플로우
```
popup.js
  → chrome.tabs.sendMessage(tabId, {type:"MYETL_COLLECT"})
      → content-bridge.js (content script)
          → myetl-collect.js (collectMyetlAssignments)
              → chrome.runtime.sendMessage({type:"MYETL_PROGRESS"}) (진행상황)
                  → background.js (service worker) [Cursor 작업]
                      → popup.js (onMessage listener)
          → sendResponse({items, courses, error})
      → popup.js
          → fetch POST /api/sync/from-client
              → Google Calendar
```

### 검수 포인트
- [ ] background.js가 MYETL_PROGRESS를 popup으로 정확히 릴레이하는가
- [ ] 아이콘 PNG가 Chrome Extension에서 정상 표시되는가
- [ ] manifest.json에 background service_worker 필드 추가되었는가

---

## 2. Electron 아키텍처

### 기존 방식 (제거 대상)
```
main.js → Selenium → myetl 로그인 (2FA 필요) → 스크래핑
```

### 새 방식 (Cursor 구현 목표)
```
main.js
  → BrowserWindow (webviewTag: true)
      → <webview src="https://myetl.snu.ac.kr/my/" partition="persist:myetl">
          (이미 로그인된 세션 재사용)
      → 버튼 클릭
          → webview.executeJavaScript(myetl-collect.js 주입)
          → collectMyetlAssignments() 실행
          → 결과 IPC로 main process에 전달
          → fetch POST /api/sync/from-client
```

### 검수 포인트
- [ ] webview partition "persist:myetl" 로 세션 유지되는가
- [ ] executeJavaScript로 myetl-collect.js 주입 후 함수 호출 가능한가
- [ ] 로그인 감지 로직 (URL 또는 DOM 확인)

---

## 3. 웹앱 Extension 안내 섹션

### 위치
`static/index.html` — "플랜" 섹션 바로 앞

### 체크
- [ ] 안내 내용이 최신 설치 방법과 일치하는가
- [ ] 디자인이 기존 카드 시스템과 통일되는가 (rounded-2xl, shadow-card)

---

## 4. 검수 기준 (Check Phase 입력값)

| 항목 | 기준 | 담당 |
|------|------|------|
| Extension 로드 오류 없음 | Chrome 확장 관리자에서 오류 0 | Claude Code 검수 |
| 로그인 → 동기화 플로우 | popup에서 로그인 → myetl 탭에서 버튼 클릭 → 결과 표시 | Claude Code 검수 |
| 마감일 파싱 정확도 | 한국어 날짜 형식 5가지 이상 커버 | Claude Code 검수 |
| Google Calendar 일정 생성 | Render 배포 환경에서 end-to-end 확인 | 사용자 확인 |
| Electron WebView 세션 유지 | 앱 재시작 시 myetl 세션 유지 | 사용자 확인 |
