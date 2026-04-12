const wv = document.getElementById("wv");
const apiEl = document.getElementById("api");
const jwtEl = document.getElementById("jwt");
const hint = document.getElementById("hint");

try {
  const raw = localStorage.getItem("etlcal_desk");
  if (raw) {
    const j = JSON.parse(raw);
    if (j.api) apiEl.value = j.api;
    if (j.jwt) jwtEl.value = j.jwt;
  }
} catch {
  /* ignore */
}

function saveLocal() {
  localStorage.setItem(
    "etlcal_desk",
    JSON.stringify({ api: apiEl.value.trim(), jwt: jwtEl.value.trim() }),
  );
}

apiEl.addEventListener("change", saveLocal);
jwtEl.addEventListener("change", saveLocal);

function waitWebviewDomReady() {
  return new Promise((resolve) => {
    if (!wv.isLoading()) resolve();
    else wv.addEventListener("dom-ready", () => resolve(), { once: true });
  });
}

document.getElementById("sync").addEventListener("click", async () => {
  const apiBase = apiEl.value.trim().replace(/\/$/, "");
  const token = jwtEl.value.trim();
  if (!apiBase || !token) {
    hint.textContent = "API 베이스와 JWT를 입력하세요.";
    return;
  }
  saveLocal();
  await waitWebviewDomReady();
  hint.textContent = "수집 중…";
  let script;
  try {
    script = await window.desk.getCollectScript();
  } catch (e) {
    hint.textContent = "수집 스크립트 로드 실패: " + String(e);
    return;
  }
  const wrapped = `(async () => {\n${script}\nreturn await collectMyetlAssignments({ delayMs: 350 });\n})()`;
  let collected;
  try {
    collected = await wv.executeJavaScript(wrapped, true);
  } catch (e) {
    hint.textContent = "WebView 실행 실패: " + String(e);
    return;
  }
  if (!collected || collected.error) {
    hint.textContent = JSON.stringify(collected || { error: "empty" });
    return;
  }
  const items = collected.items || [];
  hint.textContent = `${items.length}건 수집, 서버 전송…`;
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
    hint.textContent = `HTTP ${res.status}: ${typeof body === "string" ? body : JSON.stringify(body)}`;
    return;
  }
  hint.textContent = JSON.stringify(body);
});
