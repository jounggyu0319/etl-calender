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

  /** 공지 posted_at이 학기 구간 안인지 (due와 동일 창) */
  function isPostedInActiveSemester(isoPosted) {
    return isDueInActiveSemester(isoPosted);
  }

  function examKindFromTitle(title) {
    const s = title || "";
    const t = s.toLowerCase();
    if (s.includes("중간고사")) return "midterm";
    if (t.includes("midterm") || t.includes("mid-term") || t.includes("mid term")) return "midterm";
    if (s.includes("중간")) return "midterm";
    if (s.includes("기말고사")) return "final";
    if (t.includes("final exam")) return "final";
    if (s.includes("기말")) return "final";
    if (/\bfinal\b/i.test(t)) return "final";
    if (s.includes("시험")) return "general";
    if (/\bexam\b/i.test(t)) return "general";
    if (/\btest\b/i.test(t)) return "general";
    return null;
  }

  function announcementMatchesExamTitle(title) {
    if (!examKindFromTitle(title)) {
      const x = title || "";
      if (/\btest\b/i.test(x.toLowerCase())) return true;
      return ["시험 안내", "시험일정", "시험 일정", "시험일"].some((k) => x.includes(k));
    }
    return true;
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

      let announcements = [];
      try {
        announcements = await fetchAllPages(
          `${origin}/api/v1/courses/${cid}/discussion_topics?only_announcements=true&per_page=50`,
          delayMs,
        );
      } catch {
        /* 권한 없거나 API 없음 */
      }

      for (const topic of announcements) {
        const title = (topic.title || "").trim();
        if (!title || !announcementMatchesExamTitle(title)) continue;
        const posted = topic.posted_at || topic.delayed_post_at;
        if (!isPostedInActiveSemester(posted)) continue;
        const tid = topic.id;
        if (tid == null) continue;
        items.push({
          id: `canvas-${cid}-announce-${tid}`,
          title,
          subject: subj,
          url:
            (topic.html_url || "").trim() ||
            `${origin}/courses/${cid}/discussion_topics/${tid}`,
          activity_type: "exam",
          deadline: "",
          posted_at: posted || "",
          description_extra: title,
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
