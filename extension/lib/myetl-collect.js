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

  /**
   * 현재 학기 코드 반환: "2026-1" (3~8월) 또는 "2026-2" (9~2월)
   */
  function currentSemesterCode() {
    const now = new Date();
    const y = now.getFullYear();
    const m = now.getMonth() + 1;
    if (m >= 3 && m <= 8) return `${y}-1`;
    if (m >= 9) return `${y}-2`;
    return `${y - 1}-2`;
  }

  /**
   * Canvas 강의가 현재 학기에 해당하는지 판단.
   * 1순위: term.start_at / end_at 으로 현재 학기 창과 겹치는지 확인
   * 2순위: 강의명에 "YYYY-N" 코드 포함 여부
   */
  function isCourseInCurrentSemester(c) {
    const [winStart, winEnd] = pickDueFilterWindowMs();
    const term = c.term;
    if (term) {
      const ts = term.start_at ? Date.parse(term.start_at) : null;
      const te = term.end_at ? Date.parse(term.end_at) : null;
      if (ts !== null && te !== null && !Number.isNaN(ts) && !Number.isNaN(te)) {
        return ts <= winEnd && te >= winStart;
      }
    }
    // fallback: "YYYY-N" prefix in course name
    const semCode = currentSemesterCode();
    const name = (c.name || c.course_code || "").trim();
    return name.startsWith(semCode) || name.includes(`[${semCode}`);
  }

  function examKindFromTitle(title) {
    const s = title || "";
    const t = s.toLowerCase();
    // 명확한 시험 용어만 매칭 — "중간"/"기말" 단독은 너무 광범위 (중간발표, 기말보고서 등 오인)
    if (s.includes("중간고사")) return "midterm";
    if (t.includes("midterm") || t.includes("mid-term") || t.includes("mid term")) return "midterm";
    if (s.includes("중간 시험") || s.includes("중간시험") || s.includes("중간 평가")) return "midterm";
    if (s.includes("기말고사")) return "final";
    if (t.includes("final exam") || t.includes("final test") || t.includes("final examination")) return "final";
    if (s.includes("기말 시험") || s.includes("기말시험") || s.includes("기말 평가")) return "final";
    if (s.includes("시험")) return "general";
    if (/\bexam\b/i.test(t)) return "general";
    if (/\btest\b/i.test(t)) return "general";
    return null;
  }

  /** 본문 평문에 날짜 힌트가 있으면 true (한국어 "N월 N일" 또는 영어 "April 22nd" 등) */
  function bodyHasDateHint(plainText) {
    if (/\d{1,2}월\s*\d{1,2}일/.test(plainText)) return true;
    if (/(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}/i.test(plainText)) return true;
    if (/\d{1,2}\s+(january|february|march|april|may|june|july|august|september|october|november|december)/i.test(plainText)) return true;
    return false;
  }

  /**
   * 공지 제목이 시험 일정 공지인지 판단.
   * "중간고사 대비용 문제", "기출 자료" 같은 자료성 공지는 제외.
   */
  function announcementMatchesExamTitle(title) {
    const t = (title || "").trim();
    // 자료·준비물·성적·결과 공지 — 시험 날짜와 무관 → 제외
    const excludeKeywords = [
      "대비용", "대비 문제", "기출문제", "기출 문제", "연습문제", "올려드렸", "자료 올",
      "성적", "결과", "레포트", "프로젝트", "project", "report",
      "발표 날짜", "날짜 배정", "발표일 배정", "수업 운영",
    ];
    const hasExamKeyword = /중간고사|기말고사|midterm|final exam/i.test(t);
    const excluded = excludeKeywords.some((k) => t.toLowerCase().includes(k.toLowerCase()));
    // 강의 운영 안내라도 제목에 시험 키워드가 함께 있으면 통과
    const isOpsOnly = !hasExamKeyword && /강의 운영|수업 운영/i.test(t);
    if (excluded || isOpsOnly) return false;

    if (!examKindFromTitle(t)) {
      if (/\btest\b/i.test(t.toLowerCase())) return true;
      return ["시험 안내", "시험일정", "시험 일정", "시험일"].some((k) => t.includes(k));
    }
    return true;
  }

  /** HTML 태그·엔티티 제거 → 평문 텍스트 */
  function stripHtml(html) {
    if (!html) return "";
    return html
      .replace(/<[^>]*>/g, " ")
      .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
      .replace(/&nbsp;/g, " ").replace(/&#\d+;/g, " ")
      .replace(/\s+/g, " ").trim();
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

  async function canvasJson(r) {
    const text = await r.text();
    // Canvas wraps responses with "while(1);" for CSRF protection
    const cleaned = text.startsWith("while(1);") ? text.slice(9) : text;
    return JSON.parse(cleaned);
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
    // SNU Canvas: name과 course_code가 동일한 문자열 → 둘 다 쓰면 두 번 표기됨
    const name = (c.name || "").trim();
    const code = (c.course_code || "").trim();
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

    // 현재 학기 강의만 필터링 (term 날짜 → YYYY-N 이름 순으로 판단)
    const semesterCourses = courses.filter(isCourseInCurrentSemester);
    // term 필터링이 아무것도 걸러내지 못하면(term 정보 없는 경우) 전체 사용
    const list = (semesterCourses.length > 0 ? semesterCourses : courses).slice(0, 60);
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
        // 본문에서 날짜 추출 시도 — 서버 parse_deadline()이 "4월 23일" 등을 인식
        const bodyText = stripHtml(topic.message || "");
        // 본문에 날짜도 없고, 제목에 "일정/안내/공지/날짜" 같은 명확한 일정어도 없으면 스킵
        const scheduleWords = ["일정", "안내", "공지", "날짜", "시간", "장소", "변경", "연기",
          "date", "time", "schedule", "when", "location", "room", "venue", "place"];
        const titleHasScheduleWord = scheduleWords.some((k) => title.toLowerCase().includes(k.toLowerCase()));
        if (!bodyHasDateHint(bodyText) && !titleHasScheduleWord) continue;

        items.push({
          id: `canvas-${cid}-announce-${tid}`,
          title,
          subject: subj,
          url:
            (topic.html_url || "").trim() ||
            `${origin}/courses/${cid}/discussion_topics/${tid}`,
          activity_type: "exam",
          deadline: bodyText.slice(0, 2000),   // parse_deadline이 날짜 추출, 없으면 posted_at fallback
          posted_at: posted || "",
          description_extra: (bodyText || title).slice(0, 7900),
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
