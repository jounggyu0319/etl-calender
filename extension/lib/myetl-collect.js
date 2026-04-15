/**
 * Canvas LMS (myetl.snu.ac.kr) — 세션 쿠키로 REST API 수집.
 * 서울대 학사일정 기준 현재 학기(또는 방학 시 가장 가까운 다음 학기)에 due_at이 있는 과제·퀴즈만 포함.
 */
(function attachGlobal() {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  /** KST 해당 일 자정(포함) ~ 말일 23:59:59 (포함) 구간의 UTC epoch ms */
  function kstRangeStartMs(y, mo, d) {
    return Date.parse(`${y}-${pad2(mo)}-${pad2(d)}T00:00:00+09:00`);
  }
  function kstRangeEndMs(y, mo, d) {
    return Date.parse(`${y}-${pad2(mo)}-${pad2(d)}T23:59:59+09:00`);
  }

  /** academicYear 학년도(Y학년도 = 3월 시작) — 2026학년도 스펙을 연도만 바꿔 확장 */
  function instructionalWindowsForYear(academicYear) {
    const y = academicYear;
    return [
      [kstRangeStartMs(y, 3, 3), kstRangeEndMs(y, 6, 21)],
      [kstRangeStartMs(y, 6, 23), kstRangeEndMs(y, 8, 3)],
      [kstRangeStartMs(y, 9, 1), kstRangeEndMs(y, 12, 21)],
      [kstRangeStartMs(y, 12, 22), kstRangeEndMs(y + 1, 1, 25)],
    ];
  }

  function pickDueFilterWindowMs() {
    const nowMs = Date.now();
    const y = new Date().getFullYear();
    const ayMin = Math.max(2026, y - 1);
    const ayMax = y + 2;
    const flat = [];
    for (let ay = ayMin; ay <= ayMax; ay++) {
      for (const w of instructionalWindowsForYear(ay)) flat.push(w);
    }
    flat.sort((a, b) => a[0] - b[0]);
    if (!flat.length) return instructionalWindowsForYear(2026)[0];
    for (const [a, b] of flat) {
      if (nowMs >= a && nowMs <= b) return [a, b];
    }
    for (const [a, b] of flat) {
      if (nowMs < a) return [a, b];
    }
    return flat[flat.length - 1];
  }

  function isDueInActiveSemester(isoDue) {
    if (isoDue == null || isoDue === "") return false;
    const t = Date.parse(isoDue);
    if (Number.isNaN(t)) return false;
    const [startMs, endMs] = pickDueFilterWindowMs();
    return t >= startMs && t <= endMs;
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

  /** Canvas는 JSON 하이재킹 방지를 위해 응답 앞에 while(1); 를 붙임 — 제거 후 파싱 */
  async function canvasJson(r) {
    const text = await r.text();
    const body = text.startsWith("while(1);") ? text.slice("while(1);".length) : text;
    return JSON.parse(body);
  }

  async function fetchAllPages(firstUrl, delayMs) {
    const rows = [];
    let url = firstUrl;
    while (url) {
      await sleep(delayMs);
      const r = await fetch(url, { credentials: "include", cache: "no-store" });
      if (r.status === 404) return rows;
      if (!r.ok) throw new Error(`HTTP ${r.status} — ${url}`);
      const chunk = await canvasJson(r);
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
        `${origin}/api/v1/courses?enrollment_state=active&include[]=term&per_page=100`,
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

    // 현재 학기 구간과 겹치는 강의만 스캔
    const [semStart, semEnd] = pickDueFilterWindowMs();

    /** 현재 학기 코드 — 예: "2026-1", "2026-2" */
    function currentSemesterPrefix() {
      const now = new Date();
      const nowMs = now.getTime();
      const y = now.getFullYear();
      const windows = instructionalWindowsForYear(y);
      // 1학기(0), 여름(1), 2학기(2), 겨울(3)
      const labels = [`${y}-1`, `${y}-여름`, `${y}-2`, `${y}-겨울`];
      for (let i = 0; i < windows.length; i++) {
        if (nowMs >= windows[i][0] && nowMs <= windows[i][1]) return labels[i];
      }
      // 방학 중이면 다음 학기
      for (let i = 0; i < windows.length; i++) {
        if (nowMs < windows[i][0]) return labels[i];
      }
      return `${y}-2`;
    }
    const semPrefix = currentSemesterPrefix(); // 예: "2026-1"

    const list = courses.filter((c) => {
      // 1) Canvas term 날짜 기준
      const termStart = c.term?.start_at ? Date.parse(c.term.start_at) : null;
      const termEnd   = c.term?.end_at   ? Date.parse(c.term.end_at)   : null;
      if (termStart !== null && termEnd !== null) {
        return termStart <= semEnd && termEnd >= semStart;
      }
      // 2) course start_at/end_at 기준
      const cStart = c.start_at ? Date.parse(c.start_at) : null;
      const cEnd   = c.end_at   ? Date.parse(c.end_at)   : null;
      if (cStart !== null && cEnd !== null) {
        return cStart <= semEnd && cEnd >= semStart;
      }
      // 3) 강의명 앞 "YYYY-N" 패턴 폴백 — 예: "2026-1 강체동역학"
      const name = (c.name || c.course_code || "").trim();
      const m = name.match(/^(\d{4}-\d)/);
      if (m) return m[1] === semPrefix;
      // 날짜·이름 정보 모두 없으면 포함
      return true;
    }).slice(0, 60);
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
        /* 퀴즈 API 없거나 권한 없음 */
      }

      const includedAssignmentIds = new Set();

      for (const a of assignments) {
        const due = a.due_at;
        if (!isDueInActiveSemester(due)) continue;
        if (a.id != null) includedAssignmentIds.add(a.id);
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
        if (!isDueInActiveSemester(due)) continue;
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
