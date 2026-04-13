const wv = document.getElementById("wv");
const apiEl = document.getElementById("api");
const jwtEl = document.getElementById("jwt");
const hint = document.getElementById("hint");
const syncBtn = document.getElementById("sync");
const dot = document.getElementById("dot");
const loginText = document.getElementById("login-text");
const loginForm = document.getElementById("login-form");
const jwtStatus = document.getElementById("jwt-status");

const DEFAULT_API = "https://etl-calendar-sync.onrender.com";

function setLoginState(ok, msg) {
  if (dot) dot.style.background = ok ? "#22c55e" : "#94a3b8";
  if (loginText) loginText.textContent = msg || (ok ? "로그인 감지됨" : "로그인 필요");
  if (syncBtn) syncBtn.disabled = !ok;
}

// ── 저장된 값 복원 ──
try {
  const raw = localStorage.getItem("etlcal_desk");
  if (raw) {
    const j = JSON.parse(raw);
    if (j.api) apiEl.value = j.api;
    if (j.jwt) {
      jwtEl.value = j.jwt;
      jwtStatus.textContent = "✅ 저장됨";
    }
  } else {
    apiEl.value = DEFAULT_API;
  }
} catch {
  /* ignore */
}
if (!apiEl.value) apiEl.value = DEFAULT_API;

// ── 서버 로그인 폼 표시 여부 ──
function updateServerLoginUI() {
  if (!jwtEl.value.trim()) {
    loginForm.style.display = "block";
  } else {
    loginForm.style.display = "none";
  }
}
updateServerLoginUI();

// ── 서버 로그인 ──
document.getElementById("btn-login").addEventListener("click", async () => {
  const email = document.getElementById("inp-email").value.trim();
  const pass  = document.getElementById("inp-pass").value;
  const apiBase = apiEl.value.trim().replace(/\/$/, "") || DEFAULT_API;
  if (!email || !pass) { hint.textContent = "이메일과 비밀번호를 입력하세요."; return; }
  hint.textContent = "서버 로그인 중…";
  try {
    const body = new URLSearchParams();
    body.set("username", email);
    body.set("password", pass);
    body.set("grant_type", "password");
    const res = await fetch(`${apiBase}/api/auth/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    jwtEl.value = data.access_token;
    jwtStatus.textContent = "✅ 로그인됨";
    apiEl.value = apiBase;
    saveLocal();
    updateServerLoginUI();
    hint.textContent = "서버 로그인 완료. myetl 로그인 후 동기화 버튼을 누르세요.";
  } catch (e) {
    hint.textContent = "서버 로그인 실패: " + (e.message || String(e));
  }
});

function saveLocal() {
  localStorage.setItem(
    "etlcal_desk",
    JSON.stringify({ api: apiEl.value.trim() || DEFAULT_API, jwt: jwtEl.value.trim() }),
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

async function detectMyetlLogin() {
  try {
    await waitWebviewDomReady();
    const ok = await wv.executeJavaScript(
      `(() => {
        const byLogout =
          !!document.querySelector('a[href*=\"logout\"], a[href*=\"/login/logout\"], form[action*=\"logout\"] button');
        const byUserMenu =
          !!document.querySelector('[data-region=\"user-menu\"], .usertext, .usermenu, #usermenu, [id*=\"user-menu\"]');
        return Boolean(byLogout || byUserMenu);
      })()`,
      true,
    );
    setLoginState(!!ok, ok ? "로그인 감지됨" : "로그인 필요");
  } catch {
    setLoginState(false, "로그인 확인 실패");
  }
}

wv.addEventListener("dom-ready", () => {
  detectMyetlLogin();
});
wv.addEventListener("did-navigate", () => {
  detectMyetlLogin();
});
wv.addEventListener("did-navigate-in-page", () => {
  detectMyetlLogin();
});

setLoginState(false, "로그인 확인 중…");
detectMyetlLogin();

syncBtn.addEventListener("click", async () => {
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
