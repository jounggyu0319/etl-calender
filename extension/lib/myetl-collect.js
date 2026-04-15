/**
 * myetl 강의별 활동(과제·퀴즈) 수집.
 * Chrome 확장 content script 환경에서 동작 (DOMParser + fetch + 쿠키 세션).
 *
 * 강의 목록 획득 전략 (순서대로):
 *  1. 현재 페이지 DOM — 네트워크 요청 없이 사이드바에서 추출
 *  2. 네트워크 폴백 — /my/ → /dashboard → /course/ 순으로 시도
 */
(function attachGlobal() {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ── 마감일 추출 ──────────────────────────────────────────────
  const DEADLINE_SELECTORS = [
    ".submissionstatustable td",
    ".generaltable td",
    ".quizattemptsummary td",
    ".quizinfo td",
    "#region-main",
    "body",
  ];

  function extractDeadlineFromDoc(doc) {
    for (const sel of DEADLINE_SELECTORS) {
      for (const el of doc.querySelectorAll(sel)) {
        const found = matchDateInText(el.textContent || "");
        if (found) return found;
      }
    }
    return "";
  }

  function matchDateInText(text) {
    if (!text) return "";
    const t = text.replace(/\n/g, " ");
    let m = t.match(/(\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*(?:오전|오후)\s*\d{1,2}:\d{2})/);
    if (m) return m[1].trim();
    m = t.match(/(\d{4}년\s*\d{1,2}월\s*\d{1,2}일)/);
    if (m) return m[1].trim();
    m = t.match(/(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2}|Z)?)/);
    if (m) return m[1].trim();
    m = t.match(/(\d{4}-\d{2}-\d{2})/);
    if (m) return m[1].trim();
    return "";
  }

  // ── 강의 목록 파싱 ────────────────────────────────────────────
  /** document 객체에서 직접 강의 링크 추출 */
  function parseCoursesFromDoc(doc) {
    const byUrl = new Map();
    for (const a of doc.querySelectorAll('a[href*="course/view.php"]')) {
      const href = (a.href || "").split("#")[0].trim();
      const name = (a.textContent || "").trim();
      if (href && name && !byUrl.has(href)) byUrl.set(href, name);
    }
    return [...byUrl.entries()].map(([url, name]) => ({ url, name }));
  }

  /** HTML 문자열에서 강의 링크 추출 */
  function parseCoursesFromHtml(html) {
    return parseCoursesFromDoc(new DOMParser().parseFromString(html, "text/html"));
  }

  // ── 강의 페이지에서 활동 링크 파싱 ──────────────────────────
  function parseActivitiesFromCourseHtml(html, courseName) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const found = new Map();
    for (const [hk, activityType] of [["mod/assign", "assign"], ["mod/quiz", "quiz"]]) {
      for (const a of doc.querySelectorAll(`a[href*="${hk}"]`)) {
        const url = (a.href || "").split("#")[0].trim();
        if (!url) continue;
        const title = (a.textContent || "").trim() || url.split("?")[0].split("/").pop() || activityType;
        if (!found.has(url)) {
          found.set(url, { id: url, title, subject: courseName, url, activity_type: activityType });
        }
      }
    }
    return [...found.values()];
  }

  // ── fetch helper ─────────────────────────────────────────────
  /** @returns {Promise<string|null>} 404면 null, 그 외 비정상은 throw */
  async function fetchText(url) {
    const r = await fetch(url, { credentials: "include", cache: "no-store" });
    if (r.status === 404) return null;
    if (!r.ok) throw new Error(`HTTP ${r.status} — ${url}`);
    return r.text();
  }

  // ── 메인 수집 함수 ────────────────────────────────────────────
  /**
   * @param {object} options
   * @param {number} [options.delayMs=300]
   * @param {function} [options.onProgress] - ({current, total, courseName})
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

    // 1) 현재 페이지 DOM에서 강의 링크 추출 (네트워크 요청 없음)
    let courses = typeof document !== "undefined" ? parseCoursesFromDoc(document) : [];

    // 2) 현재 페이지에 강의 없으면 네트워크 폴백
    if (courses.length === 0) {
      for (const path of ["/my/", "/dashboard", "/course/"]) {
        try {
          const html = await fetchText(`${origin}${path}`);
          if (html != null) {
            courses = parseCoursesFromHtml(html);
            if (courses.length > 0) break;
          }
        } catch {
          // 다음 경로 시도
        }
      }
    }

    if (courses.length === 0) {
      return {
        error: "강의 목록을 찾을 수 없습니다. myetl에 로그인 후 강의가 있는 페이지에서 시도해주세요.",
        items: [],
        courses: 0,
      };
    }

    courses = courses.slice(0, 60);
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
      if (courseHtml == null) { coursesSkipped++; continue; }

      for (const act of parseActivitiesFromCourseHtml(courseHtml, c.name)) {
        await sleep(delayMs);
        let deadline = "";
        try {
          const body = await fetchText(act.url);
          if (body != null) {
            deadline = extractDeadlineFromDoc(new DOMParser().parseFromString(body, "text/html"));
          }
        } catch {
          // 마감일 없이도 수집 계속
        }
        items.push({ ...act, deadline });
      }
    }

    return { error: null, items, courses: courses.length, coursesSkipped };
  }

  // ── 전역 노출 ─────────────────────────────────────────────────
  const root = typeof globalThis !== "undefined" ? globalThis : typeof window !== "undefined" ? window : {};
  root.collectMyetlAssignments = collectMyetlAssignments;
})();
