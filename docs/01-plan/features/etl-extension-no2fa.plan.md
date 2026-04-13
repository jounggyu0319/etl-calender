# etl-extension-no2fa Planning Document

> **Summary**: 이미 로그인된 브라우저 탭을 활용해 2FA 없이 eTL 과제·퀴즈를 Google Calendar에 동기화하는 Chrome Extension 완성
>
> **Project**: eTL Calendar Sync
> **Version**: 0.2.0
> **Author**: jounggyu
> **Date**: 2026-04-13
> **Status**: In Progress (Do Phase)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | Selenium 방식은 새 브라우저를 열어 SNU 통합로그인 + 2FA를 매번 요구 → 실사용 불가 |
| **Solution** | Chrome Extension이 이미 로그인된 myetl 탭에 content script로 접근해 데이터 수집 |
| **Function/UX Effect** | 클릭 한 번으로 동기화 완료. 별도 로그인·2FA 불필요. 서버에 Selenium 의존성 제거 |
| **Core Value** | 서울대학교 학생이 과제 마감일을 자동으로 Google Calendar에서 관리 |

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 2FA로 인한 Selenium 기반 동기화 실사용 불가 문제 해결 |
| **WHO** | 서울대학교 재학생 (myetl 사용자) |
| **RISK** | myetl DOM 구조 변경 시 content script 수집 실패 가능성 |
| **SUCCESS** | myetl 탭 열린 상태에서 클릭 한 번 → Google Calendar 일정 생성 |
| **SCOPE** | Phase 1: Extension 완성 / Phase 2: Electron 앱 / Phase 3: 웹앱 UI |

---

## 2. Scope

### 2.1 In Scope (Claude Code 완료)
- [x] `extension/manifest.json` — MV3, tabs 권한, 아이콘 경로
- [x] `extension/lib/myetl-collect.js` — 마감일 추출 개선, 진행상황 콜백
- [x] `extension/content-bridge.js` — PING(로그인 확인) + COLLECT + PROGRESS 메시지
- [x] `extension/popup.html` — 로그인 폼, 상태바, 진행바 UI
- [x] `extension/popup.js` — JWT 자동저장, 탭 감지, 동기화 로직
- [x] `render.yaml` — `--proxy-headers` (Google OAuth Render 버그 수정)
- [x] `app/routers/google_oauth.py` — HTTPS 스킴 보정 + 에러 로깅

### 2.2 Cursor 담당 (진행 중)
- [ ] `extension/background.js` — service worker (PROGRESS 메시지 릴레이)
- [ ] `extension/icons/` — PNG 아이콘 (16, 48, 128px)
- [ ] `desktop/` — Electron WebView 방식 전환
- [ ] `static/index.html` — Extension 안내 섹션 + 동기화 히스토리

### 2.3 Out of Scope
- Chrome Web Store 등록 (개인 배포 수준)
- Safari Extension
- 자동 스케줄링 (추후 Phase)

---

## 3. Architecture Decision

```
[사용자 브라우저]
  myetl 탭 (이미 로그인됨)
    └── content script (myetl-collect.js + content-bridge.js)
         ↑ chrome.tabs.sendMessage
  Extension Popup (popup.html + popup.js)
         ↓ fetch (CORS OK: allow_origins=["*"])
[FastAPI 서버]
  POST /api/sync/from-client
         ↓
  Google Calendar API
```

---

## 4. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| myetl DOM 변경 | Medium | CSS 셀렉터 다중 fallback 적용 |
| Google OAuth Render HTTPS 불일치 | High | ✅ 수정 완료 (proxy-headers) |
| Extension content script 미로드 | Low | PING으로 사전 확인 + 새로고침 안내 |
| Cursor 작업 품질 미흡 | Medium | Claude Code가 검수 예정 |

---

## 5. Success Criteria

- [ ] Extension 로드 후 로그인 → myetl 탭에서 동기화 → Google Calendar 일정 생성 확인
- [ ] Google OAuth Render 연동 정상 동작
- [ ] popup에서 진행 상황 표시 (강의 N/M 스캔)
- [ ] Electron 앱에서 2FA 없이 동기화 가능
