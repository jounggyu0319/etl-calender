/**
 * eTL Calendar Chrome Extension — popup.js
 *
 * 상태 흐름:
 *   1. 스토리지에서 apiBase·jwt 로드
 *   2. jwt로 /api/me/ 호출 → 유효하면 sync 섹션 표시
 *   3. 무효/없으면 login 섹션 표시
 *   4. 현재 탭이 myetl.snu.ac.kr인지 확인 → 버튼 활성화
 *   5. 동기화: content script에 MYETL_COLLECT 메시지 → 결과를 /api/sync/from-client로 POST
 */

const DEFAULT_API = "https://etl-calendar-sync.onrender.com";

// ── DOM helpers ──────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const show = (id) => { const el = $(id); if (el) el.style.display = ""; };
const hide = (id) => { const el = $(id); if (el) el.style.display = "none"; };

function setStatus(text, type /* ok | err | warn */) {
  $("status-text").textContent = text;
  const dot = $("status-dot");
  dot.className = "dot " + (type === "ok" ? "dot-ok" : type === "warn" ? "dot-warn" : "dot-err");
}

function showResult(text, isOk) {
  const el = $("result");
  el.textContent = text;
  el.className = isOk ? "ok" : "err";
  el.style.display = "block";
}

function hideResult() {
  $("result").style.display = "none";
}

function setProgress(text, pct) {
  const wrap = $("progress");
  wrap.style.display = "block";
  $("progress-text").textContent = text;
  if (typeof pct === "number") {
    $("progress-bar").style.width = Math.min(100, pct) + "%";
  }
}

function hideProgress() {
  $("progress").style.display = "none";
}

// ── Storage ──────────────────────────────────────────────────
async function loadStorage() {
  return chrome.storage.local.get(["apiBase", "jwt"]);
}

async function saveStorage(data) {
  return chrome.storage.local.set(data);
}

// ── API calls ────────────────────────────────────────────────
async function apiFetch(apiBase, path, opts = {}, token = null) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (token) headers["Authorization"] = "Bearer " + token;
  const res = await fetch(apiBase.replace(/\/$/, "") + path, {
    ...opts,
    headers,
  });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    const msg = (data && (data.detail || data.message)) || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

async function fetchMe(apiBase, token) {
  return apiFetch(apiBase, "/api/me/", {}, token);
}

async function doLogin(apiBase, email, password) {
  const body = new URLSearchParams();
  body.set("username", email);
  body.set("password", password);
  body.set("grant_type", "password");
  return apiFetch(apiBase, "/api/auth/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
}

async function postFromClient(apiBase, token, items) {
  return apiFetch(apiBase, "/api/sync/from-client", {
    method: "POST",
    body: JSON.stringify({ items }),
  }, token);
}

// ── Tab helpers ──────────────────────────────────────────────
async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab || null;
}

function isMyetlTab(tab) {
  return tab && tab.url && tab.url.includes("myetl.snu.ac.kr");
}

async function findMyetlTab() {
  const tabs = await chrome.tabs.query({ url: "https://myetl.snu.ac.kr/*" });
  return tabs[0] || null;
}

// ── Main logic ───────────────────────────────────────────────
let _apiBase = DEFAULT_API;
let _jwt = "";
let _me = null;

async function init() {
  const s = await loadStorage();
  _apiBase = (s.apiBase || DEFAULT_API).replace(/\/$/, "");
  _jwt = s.jwt || "";

  // 설정 입력 초기화
  $("set-api").value = _apiBase === DEFAULT_API ? "" : _apiBase;

  // 웹앱 링크
  $("link-open-app").onclick = (e) => {
    e.preventDefault();
    chrome.tabs.create({ url: _apiBase });
  };

  if (!_jwt) {
    showLoginSection("서버에 로그인 해주세요.");
    return;
  }

  setStatus("서버 연결 확인 중…", "warn");
  try {
    _me = await fetchMe(_apiBase, _jwt);
    await showSyncSection();
  } catch (e) {
    showLoginSection("세션이 만료되었습니다. 다시 로그인해 주세요.");
  }
}

function showLoginSection(statusMsg) {
  setStatus(statusMsg || "로그인이 필요합니다.", "err");
  hide("sync-section");
  show("login-section");
}

async function showSyncSection() {
  hide("login-section");
  show("sync-section");

  if (!_me) {
    setStatus("오류: 사용자 정보 없음", "err");
    return;
  }

  const googleOk = _me.has_google;
  if (!googleOk) {
    setStatus("Google 캘린더 미연동 — 웹앱에서 연동해주세요", "warn");
  } else {
    setStatus(`로그인됨 (${_me.email})`, "ok");
  }

  // 현재 탭 확인
  await checkCurrentTab();
}

async function checkCurrentTab() {
  const tab = await getActiveTab();
  const onMyetl = isMyetlTab(tab);
  const myetlStatus = $("myetl-status");
  const btnSync = $("btn-sync");
  const btnOpenMyetl = $("btn-open-myetl");

  if (onMyetl) {
    myetlStatus.textContent = "✅ myetl 탭 감지됨 — 바로 동기화할 수 있습니다.";
    myetlStatus.style.color = "#15803d";
    btnSync.disabled = !_me?.has_google;
    hide("btn-open-myetl");
  } else {
    // 다른 탭에서 myetl 탭 검색
    const myetlTab = await findMyetlTab();
    if (myetlTab) {
      myetlStatus.textContent = "📌 myetl 탭이 열려 있습니다. 해당 탭에서 수집합니다.";
      myetlStatus.style.color = "#1e40af";
      btnSync.disabled = !_me?.has_google;
      hide("btn-open-myetl");
    } else {
      myetlStatus.textContent = "⚠️ myetl 탭이 없습니다. 아래 버튼으로 열어주세요.";
      myetlStatus.style.color = "#b45309";
      btnSync.disabled = true;
      show("btn-open-myetl");
    }
  }
}

// ── 이벤트 바인딩 ────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  init();

  // 로그인
  $("btn-login").onclick = async () => {
    const email = $("inp-email").value.trim();
    const pass  = $("inp-pass").value;
    if (!email || !pass) {
      setStatus("이메일과 비밀번호를 입력해주세요.", "err");
      return;
    }
    $("btn-login").disabled = true;
    setStatus("로그인 중…", "warn");
    try {
      const data = await doLogin(_apiBase, email, pass);
      _jwt = data.access_token;
      await saveStorage({ jwt: _jwt, apiBase: _apiBase });
      _me = await fetchMe(_apiBase, _jwt);
      await showSyncSection();
    } catch (e) {
      setStatus("로그인 실패: " + (e.message || ""), "err");
    } finally {
      $("btn-login").disabled = false;
    }
  };

  // 로그아웃
  $("btn-logout").onclick = async () => {
    _jwt = "";
    _me = null;
    await saveStorage({ jwt: "" });
    showLoginSection("로그아웃되었습니다.");
  };

  // myetl 열기
  $("btn-open-myetl").onclick = () => {
    chrome.tabs.create({ url: "https://myetl.snu.ac.kr/my/" });
  };

  // 동기화
  $("btn-sync").onclick = async () => {
    hideResult();
    hideProgress();
    $("btn-sync").disabled = true;
    setProgress("myetl 탭에서 수집 시작…", 0);

    try {
      // myetl 탭 찾기 (현재 탭 우선, 없으면 전체 탭에서 검색)
      let targetTab = await getActiveTab();
      if (!isMyetlTab(targetTab)) {
        targetTab = await findMyetlTab();
      }
      if (!targetTab) {
        showResult("myetl 탭이 없습니다. myetl을 먼저 열어주세요.", false);
        return;
      }

      // content script에 수집 요청
      let collected;
      try {
        collected = await chrome.tabs.sendMessage(targetTab.id, {
          type: "MYETL_COLLECT",
          delayMs: 300,
        });
      } catch (e) {
        showResult(
          "페이지를 새로고침(F5) 한 뒤 다시 시도해주세요.\n상세: " + String(e),
          false
        );
        return;
      }

      if (!collected || collected.error) {
        showResult("수집 실패: " + (collected?.error || "응답 없음"), false);
        return;
      }

      const items = collected.items || [];
      const courseCount = collected.courses || 0;
      const skipped = collected.coursesSkipped || 0;
      const partialHead =
        skipped > 0
          ? items.length > 0
            ? `일부 강의를 불러오지 못했지만 ${items.length}건 수집됨`
            : "일부 강의를 불러오지 못했습니다 (새 항목 없음)"
          : "";
      setProgress(
        (partialHead ? partialHead + " · " : "") +
          `${courseCount}개 강의에서 ${items.length}건 수집. 서버 전송 중…`,
        90
      );

      // 서버 전송 (빈 items도 전송 → 서버/📭 새 항목 없음 처리)
      const result = await postFromClient(_apiBase, _jwt, items);
      hideProgress();

      const added = (result.calendar_events_created || 0) + (result.ics_events_created || 0);
      const parts = [
        partialHead,
        `강의 ${result.courses_found || courseCount}개 확인`,
        `새 항목 ${result.new_assignments || 0}건`,
        `캘린더 추가 ${added}건`,
        result.message || "",
      ].filter(Boolean);
      const msg = parts.join(" · ");
      showResult((added > 0 ? "✅ " : "📭 ") + msg, true);
    } catch (e) {
      hideProgress();
      showResult("오류: " + (e.message || String(e)), false);
    } finally {
      $("btn-sync").disabled = false;
      await checkCurrentTab();
    }
  };

  // 설정 저장
  $("btn-save-settings").onclick = async () => {
    const newApi = $("set-api").value.trim() || DEFAULT_API;
    _apiBase = newApi.replace(/\/$/, "");
    await saveStorage({ apiBase: _apiBase });
    setStatus("서버 주소 저장됨. 다시 로그인해주세요.", "warn");
    _jwt = "";
    _me = null;
    showLoginSection();
  };

  $("btn-reset-settings").onclick = async () => {
    $("set-api").value = "";
    _apiBase = DEFAULT_API;
    await saveStorage({ apiBase: DEFAULT_API });
    setStatus("기본 서버로 초기화됨.", "warn");
  };

  // 진행상황 메시지 수신 (content script → background → popup)
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === "MYETL_PROGRESS") {
      const pct = msg.total > 0 ? Math.round((msg.current / msg.total) * 80) : 0;
      setProgress(
        `강의 ${msg.current}/${msg.total} 스캔 중… ${msg.courseName || ""}`,
        pct
      );
    }
  });
});
