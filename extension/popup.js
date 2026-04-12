const apiEl = document.getElementById("api");
const jwtEl = document.getElementById("jwt");
const logEl = document.getElementById("log");

function log(msg) {
  logEl.textContent = typeof msg === "string" ? msg : JSON.stringify(msg, null, 2);
}

async function load() {
  const s = await chrome.storage.local.get(["apiBase", "jwt"]);
  if (s.apiBase) apiEl.value = s.apiBase;
  if (s.jwt) jwtEl.value = s.jwt;
}

document.getElementById("save").onclick = async () => {
  await chrome.storage.local.set({
    apiBase: apiEl.value.trim(),
    jwt: jwtEl.value.trim(),
  });
  log("저장했습니다.");
};

async function ensureOriginPermission(apiBase) {
  let u;
  try {
    u = new URL(apiBase);
  } catch {
    return;
  }
  const pattern = `${u.origin}/*`;
  try {
    const ok = await chrome.permissions.contains({ origins: [pattern] });
    if (!ok) await chrome.permissions.request({ origins: [pattern] });
  } catch {
    /* 사용자 거부 시 fetch가 실패할 수 있음 */
  }
}

document.getElementById("run").onclick = async () => {
  const apiBase = apiEl.value.trim().replace(/\/$/, "");
  const token = jwtEl.value.trim();
  if (!apiBase || !token) {
    log("API 베이스와 JWT를 입력하세요.");
    return;
  }
  await ensureOriginPermission(apiBase);
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    log("활성 탭을 찾을 수 없습니다.");
    return;
  }
  if (!tab.url || !tab.url.includes("myetl.snu.ac.kr")) {
    log("myetl.snu.ac.kr 탭을 연 뒤 다시 시도하세요.");
    return;
  }
  log("수집 중… (강의 수에 따라 수십 초~수분 걸릴 수 있음)");
  let collected;
  try {
    collected = await chrome.tabs.sendMessage(tab.id, { type: "MYETL_COLLECT" });
  } catch (e) {
    log("콘텐츠 스크립트 없음: 페이지를 새로고침한 뒤 다시 시도하세요.\n" + String(e));
    return;
  }
  if (!collected || collected.error) {
    log(collected || { error: "no response" });
    return;
  }
  const items = collected.items || [];
  log(items.length + "건 수집, 서버 전송 중…");
  const url = `${apiBase}/api/sync/from-client`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ items }),
  });
  const text = await res.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    body = text;
  }
  if (!res.ok) {
    log({ status: res.status, body });
    return;
  }
  log(body);
};

load();
