/**
 * content script ↔ popup 메시지 브릿지.
 * MYETL_COLLECT: 수집 시작 → 결과 반환
 * MYETL_PING:    로그인 여부 확인
 */
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg) return false;

  // ── 로그인 상태 확인 ─────────────────────────────────────────
  if (msg.type === "MYETL_PING") {
    // /my/ 가 200이면 로그인됨
    fetch("https://myetl.snu.ac.kr/my/", { credentials: "include", cache: "no-store" })
      .then((r) => sendResponse({ loggedIn: r.ok, status: r.status }))
      .catch((e) => sendResponse({ loggedIn: false, error: String(e) }));
    return true; // async
  }

  // ── 과제 수집 ────────────────────────────────────────────────
  if (msg.type === "MYETL_COLLECT") {
    const run =
      typeof collectMyetlAssignments === "function" ? collectMyetlAssignments : null;
    if (!run) {
      sendResponse({ error: "collectMyetlAssignments 미로드 — 페이지 새로고침 후 재시도", items: [], courses: 0 });
      return false;
    }
    const delayMs =
      typeof msg.delayMs === "number" && msg.delayMs >= 0 ? msg.delayMs : 300;

    run({
      delayMs,
      onProgress: (p) => {
        // 진행상황은 별도 메시지로 popup에 전달
        try {
          chrome.runtime.sendMessage({ type: "MYETL_PROGRESS", ...p });
        } catch {
          /* popup이 닫혔을 수 있음 — 무시 */
        }
      },
    })
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ error: String(e), items: [], courses: 0 }));
    return true; // async
  }

  return false;
});
