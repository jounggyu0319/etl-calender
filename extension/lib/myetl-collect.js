/**
 * Canvas LMS (myetl.snu.ac.kr) — 세션 쿠키로 REST API 수집.
 * 이번 학기(2026-03-01 ~ 2026-08-31, KST)에 마감(due_at)이 있는 과제·퀴즈만 포함.
 *
 * Canvas Assignments API에는 due_after 등 학기 단위 필터가 없어, 전부 받은 뒤 클라이언트에서 필터합니다.
 * (bucket=upcoming 은 «아직 제출 전·곧 마감» 위주라 학기 전체와 맞지 않아 사용하지 않음.)
 */
(function attachGlobal() {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  /** 2026학년도 1학기 (가정): KST 자정 기준 구간 */
  const SEMESTER_START_MS = Date.parse("2026-03-01T00:00:00+09:00");
  const SEMESTER_END_MS = Date.parse("2026-08-31T23:59:59.999+09:00");

  function isDueInSemester(isoDue) {
    if (isoDue == null || isoDue === "") return false;
    const t = Date.parse(isoDue);
    if (Number.isNaN(t)) return false;
    return t >= SEMESTER_START_MS && t <= SEMESTER_END_MS;
  }

  function parseNextFromLink(linkHeader) {
    if (!linkHeader) return null;
    const parts = linkHeader.split(",");
    for (const p of parts) {
      const m = p.match(/<([^>]+)>;\s*rel="next"/);
      if (m) return m[1].trim();
    }
    return null;
  }

  /**
   * @param {string} firstUrl
   * @param {number} delayMs
   * @returns {Promise<any[]>}
   */
  async function fetchAllPages(firstUrl, delayMs) {
    const rows = [];
    let url = firstUrl;
    while (url) {
      await sleep(delayMs);
      const r = await fetch(url, { credentials: "include", cache: "no-store" });
      if (r.status === 404) return rows;
      if (!r.ok) throw new Error(`HTTP ${r.status} — ${url}`);
      const chunk = await r.json();
      if (!Array.isArray(chunk)) break;
      rows.push(...chunk);
      url = parseNextFromLink(r.headers.get("Link"));
    }
    return rows;
  }

  function courseLabel(c) {
    const code = (c.course_code || "").trim();
    const name = (c.name || "").trim();
    if (code && name) return `${code} ${name}`;
    return name || code || `Course ${c.id}`;
  }

  // ── 메인 수집 함수 ────────────────────────────────────────────
  /**
   * @param {object} options
   * @param {number} [options.delayMs=300]
   * @param {function} [options.onProgress]
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

    let courses;
    try {
      courses = await fetchAllPages(
        `${origin}/api/v1/courses?enrollment_state=active&per_page=100`,
        delayMs,
      );
    } catch (e) {
      return { error: `강의 목록 API 실패: ${e}`, items: [], courses: 0 };
    }

    if (!courses.length) {
      return {
        error: "수강 중인 강의가 없습니다. myetl에 로그인되어 있는지 확인하세요.",
        items: [],
        courses: 0,
      };
    }

    const list = courses.slice(0, 60);
    const items = [];
    let coursesSkipped = 0;
    let idx = 0;

    for (const c of list) {
      idx++;
      if (onProgress) onProgress({ current: idx, total: list.length, courseName: courseLabel(c) });
      const subj = courseLabel(c);
      const cid = c.id;

      let assignments = [];
      let quizzes = [];
      try {
        assignments = await fetchAllPages(
          `${origin}/api/v1/courses/${cid}/assignments?per_page=100`,
          delayMs,
        );
      } catch {
        coursesSkipped++;
        continue;
      }
      try {
        quizzes = await fetchAllPages(
          `${origin}/api/v1/courses/${cid}/quizzes?per_page=100`,
          delayMs,
        );
      } catch {
        /* 퀴즈 API 없거나 권한 없음 — 과제만 */
      }

      const includedAssignmentIds = new Set();

      for (const a of assignments) {
        const due = a.due_at;
        if (!isDueInSemester(due)) continue;
        includedAssignmentIds.add(a.id);
        items.push({
          id: `canvas-${cid}-assign-${a.id}`,
          title: (a.name || "과제").trim(),
          subject: subj,
          url: (a.html_url || "").trim() || `${origin}/courses/${cid}/assignments/${a.id}`,
          activity_type: "assign",
          deadline: due,
        });
      }

      for (const q of quizzes) {
        if (q.assignment_id != null && includedAssignmentIds.has(q.assignment_id)) continue;
        const due = q.due_at;
        if (!isDueInSemester(due)) continue;
        items.push({
          id: `canvas-${cid}-quiz-${q.id}`,
          title: (q.title || "퀴즈").trim(),
          subject: subj,
          url: (q.html_url || "").trim() || `${origin}/courses/${cid}/quizzes/${q.id}`,
          activity_type: "quiz",
          deadline: due,
        });
      }
    }

    return {
      error: null,
      items,
      courses: list.length,
      coursesSkipped,
    };
  }

  const root =
    typeof globalThis !== "undefined"
      ? globalThis
      : typeof window !== "undefined"
        ? window
        : {};
  root.collectMyetlAssignments = collectMyetlAssignments;
})();
