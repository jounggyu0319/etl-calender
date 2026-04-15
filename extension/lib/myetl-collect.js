/**
 * Canvas LMS (myetl.snu.ac.kr) 강의별 과제·퀴즈 수집.
 * Chrome 확장 content script 환경 — 세션 쿠키로 Canvas REST API 호출.
 *
 * API 엔드포인트:
 *   GET /api/v1/courses?enrollment_state=active&per_page=100
 *   GET /api/v1/courses/:id/assignments?per_page=100
 *   GET /api/v1/courses/:id/quizzes?per_page=100
 */
(function attachGlobal() {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ── Canvas API fetch ──────────────────────────────────────────
  async function apiFetch(url) {
    const res = await fetch(url, { credentials: "include", cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status} — ${url}`);
    return res.json();
  }

  /** 페이지네이션 전체 수집 (Link 헤더 기반) */
  async function fetchAllPages(url) {
    const results = [];
    let next = url;
    while (next) {
      const res = await fetch(next, { credentials: "include", cache: "no-store" });
      if (!res.ok) break;
      const data = await res.json();
      if (Array.isArray(data)) results.push(...data);
      // Canvas Link header: <url>; rel="next"
      const link = res.headers.get("Link") || "";
      const match = link.match(/<([^>]+)>;\s*rel="next"/);
      next = match ? match[1] : null;
    }
    return results;
  }

  // ── 마감일 파싱 ───────────────────────────────────────────────
  /** Canvas API의 due_at (ISO 8601) → 표시용 문자열 */
  function formatDueAt(dueAt) {
    if (!dueAt) return "";
    // ISO 문자열 그대로 반환 (서버에서 파싱)
    return dueAt;
  }

  // ── 메인 수집 함수 ────────────────────────────────────────────
  /**
   * @param {object} options
   * @param {number} [options.delayMs=200]
   * @param {function} [options.onProgress] - ({current, total, courseName})
   * @returns {Promise<{error:string|null, items:Array, courses:number, coursesSkipped?:number}>}
   */
  async function collectMyetlAssignments(options) {
    const opts = options || {};
    const delayMs = typeof opts.delayMs === "number" ? opts.delayMs : 200;
    const onProgress = typeof opts.onProgress === "function" ? opts.onProgress : null;

    const origin =
      typeof location !== "undefined" && location.origin
        ? location.origin
        : "https://myetl.snu.ac.kr";

    // 1) 수강 강의 목록
    let courses;
    try {
      courses = await fetchAllPages(
        `${origin}/api/v1/courses?enrollment_state=active&per_page=100`
      );
    } catch (e) {
      return { error: `강의 목록 로드 실패: ${e}`, items: [], courses: 0 };
    }

    // 학생으로 등록된 강의만, 최대 60개
    courses = courses
      .filter((c) => c.id && c.name && !c.access_restricted_by_date)
      .slice(0, 60);

    if (courses.length === 0) {
      return {
        error: "수강 중인 강의가 없습니다. myetl에 로그인되어 있는지 확인해주세요.",
        items: [],
        courses: 0,
      };
    }

    const items = [];
    let coursesSkipped = 0;
    let idx = 0;

    for (const course of courses) {
      idx++;
      if (onProgress) onProgress({ current: idx, total: courses.length, courseName: course.name });
      await sleep(delayMs);

      // 2) 과제 목록
      try {
        const assignments = await fetchAllPages(
          `${origin}/api/v1/courses/${course.id}/assignments?per_page=100`
        );
        for (const a of assignments) {
          if (!a.id || !a.name) continue;
          items.push({
            id: `assign-${a.id}`,
            title: a.name,
            subject: course.name,
            url: a.html_url || `${origin}/courses/${course.id}/assignments/${a.id}`,
            activity_type: "assign",
            deadline: formatDueAt(a.due_at),
          });
        }
      } catch {
        coursesSkipped++;
      }

      await sleep(delayMs);

      // 3) 퀴즈 목록
      try {
        const quizzes = await fetchAllPages(
          `${origin}/api/v1/courses/${course.id}/quizzes?per_page=100`
        );
        for (const q of quizzes) {
          if (!q.id || !q.title) continue;
          items.push({
            id: `quiz-${q.id}`,
            title: q.title,
            subject: course.name,
            url: q.html_url || `${origin}/courses/${course.id}/quizzes/${q.id}`,
            activity_type: "quiz",
            deadline: formatDueAt(q.due_at),
          });
        }
      } catch {
        // 퀴즈 없어도 계속
      }
    }

    return { error: null, items, courses: courses.length, coursesSkipped };
  }

  // ── 전역 노출 ─────────────────────────────────────────────────
  const root = typeof globalThis !== "undefined" ? globalThis : typeof window !== "undefined" ? window : {};
  root.collectMyetlAssignments = collectMyetlAssignments;
})();
