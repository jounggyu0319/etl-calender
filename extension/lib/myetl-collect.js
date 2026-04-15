/**
 * myetl 동일 출처 fetch로 /my/ → 강의별 활동(과제·퀴즈) 수집.
 * Chrome 확장 content script 환경에서 동작 (DOMParser + fetch + 쿠키 세션).
 */
(function attachGlobal() {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ── 마감일 추출 ──────────────────────────────────────────────
  const DEADLINE_SELECTORS = [
    // 과제 상세 페이지
    ".submissionstatustable td",
    ".generaltable td",
    // 퀴즈 상세 페이지
    ".quizattemptsummary td",
    ".quizinfo td",
    // 공통
    "#region-main",
    "body",
  ];

  function extractDeadlineFromDoc(doc) {
    // 1) 선택자별로 텍스트 모아서 날짜 패턴 검색
    for (const sel of DEADLINE_SELECTORS) {
      for (const el of doc.querySelectorAll(sel)) {
        const text = el.textContent || "";
        const found = matchDateInText(text);
        if (found) return found;
      }
    }
    return "";
  }

  function matchDateInText(text) {
    if (!text) return "";
    const t = text.replace(/\n/g, " ");
    // 한국어 날짜+시간: 2025년 4월 30일 오후 11:59
    let m = t.match(/(\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*(?:오전|오후)\s*\d{1,2}:\d{2})/);
    if (m) return m[1].trim();
    // 한국어 날짜만: 2025년 4월 30일
    m = t.match(/(\d{4}년\s*\d{1,2}월\s*\d{1,2}일)/);
    if (m) return m[1].trim();
    // ISO-8601: 2025-04-30T23:59
    m = t.match(/(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2}|Z)?)/);
    if (m) return m[1].trim();
    // YYYY-MM-DD
    m = t.match(/(\d{4}-\d{2}-\d{2})/);
    if (m) return m[1].trim();
    return "";
  }

  // ── 강의 목록 파싱 ────────────────────────────────────────────
  function parseCoursesFromMyHtml(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const byUrl = new Map();
    for (const a of doc.querySelectorAll('a[href*="course/view.php"]')) {
      const href = (a.href || "").split("#")[0].trim();
      const name = (a.textContent || "").trim();
      if (href && name && !byUrl.has(href)) byUrl.set(href, name);
    }
    return [...byUrl.entries()].map(([url, name]) => ({ url, name }));
  }

  // ── 강의 페이지에서 활동 링크 파싱 ──────────────────────────
  function parseActivitiesFromCourseHtml(html, courseName) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const found = new Map();
    const pairs = [
      ["mod/assign", "assign"],
      ["mod/quiz", "quiz"],
    ];
    for (const [hk, activityType] of pairs) {
      for (const a of doc.querySelectorAll(`a[href*="${hk}"]`)) {
        const url = (a.href || "").split("#")[0].trim();
        let title = (a.textContent || "").trim();
        if (!url) continue;
        if (!title) title = url.split("?")[0].split("/").pop() || activityType;
        if (!found.has(url)) {
          found.set(url, {
            id: url,
            title,
            subject: courseName,
            url,
            activity_type: activityType,
          });
        }
      }
    }
    return [...found.values()];
  }

  // ── fetch helper ─────────────────────────────────────────────
  /** @returns {Promise<string|null>} 404면 null(호출부에서 건너뜀), 그 외 비정상은 throw */
  async function fetchText(url) {
    const r = await fetch(url, { credentials: "include", cache: "no-store" });
    if (r.status === 404) return null;
    if (!r.ok) throw new Error(`HTTP ${r.status} — ${url}`);
    return r.text();
  }

  // ── 메인 수집 함수 ────────────────────────────────────────────
  /**
   * @param {object} options
   * @param {number} [options.delayMs=300] - 요청 간 딜레이(ms). 너무 짧으면 서버 부하.
   * @param {function} [options.onProgress] - ({current, total, courseName}) 콜백
   * @returns {Promise<{error:string|null, items:Array, courses:number, coursesSkipped?:number}>}
   */
  async function collectMyetlAssignments(options) {
    const opts = options || {};
    const delayMs = typeof opts.delayMs === "number" ? opts.delayMs : 300;
    const onProgress = typeof opts.onProgress === "function" ? opts.onProgress : null;

    const origin =
      typeof location !== "undefined" && location.origin
        ? location.origin
        : "https://myetl.snu.ac.kr";

    // 1) 강의 목록 페이지 — /my/ → /my/index.php → /dashboard 순으로 시도
    const dashCandidates = ["/my/", "/my/index.php", "/dashboard"];
    let myHtml = null;
    let dashErr = null;
    for (const path of dashCandidates) {
      try {
        const html = await fetchText(`${origin}${path}`);
        if (html != null) { myHtml = html; break; }
      } catch (e) {
        dashErr = e;
      }
    }
    if (myHtml == null) {
      const reason = dashErr ? String(dashErr) : "404";
      return {
        error: `강의 목록 로드 실패 (${reason}). myetl에 로그인되어 있는지 확인하세요.`,
        items: [],
        courses: 0,
      };
    }

    const courses = parseCoursesFromMyHtml(myHtml).slice(0, 60);
    if (courses.length === 0) {
      return {
        error: "강의 목록이 없습니다. myetl에 로그인되어 있는지 확인하세요.",
        items: [],
        courses: 0,
      };
    }

    const items = [];
    let coursesSkipped = 0;
    let idx = 0;
    for (const c of courses) {
      idx++;
      if (onProgress) onProgress({ current: idx, total: courses.length, courseName: c.name });
      await sleep(delayMs);

      let courseHtml;
      try {
        courseHtml = await fetchText(c.url);
      } catch {
        coursesSkipped++;
        continue;
      }
      if (courseHtml == null) {
        coursesSkipped++;
        continue;
      }

      const acts = parseActivitiesFromCourseHtml(courseHtml, c.name);
      for (const act of acts) {
        await sleep(delayMs);
        let deadline = "";
        try {
          const body = await fetchText(act.url);
          if (body != null) {
            const doc = new DOMParser().parseFromString(body, "text/html");
            deadline = extractDeadlineFromDoc(doc);
          }
        } catch {
          // 마감일 없이도 수집은 계속
        }
        items.push({ ...act, deadline });
      }
    }

    // error: null + items=[] → 서버로 빈 배열 전송 → "📭 새 항목 없음" 등으로 처리 (수집 실패 아님)
    return {
      error: null,
      items,
      courses: courses.length,
      coursesSkipped,
    };
  }

  // ── 전역 노출 ─────────────────────────────────────────────────
  const root =
    typeof globalThis !== "undefined"
      ? globalThis
      : typeof window !== "undefined"
        ? window
        : {};
  root.collectMyetlAssignments = collectMyetlAssignments;
})();
