/**
 * myetl 동일 출처 fetch로 /my/ → 강의별 활동(과제·퀴즈) 수집.
 * Electron webview·Chrome 확장 content 환경에서 동작 (DOMParser + fetch).
 */
(function attachGlobal() {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function extractDeadlineSnippet(raw) {
    if (!raw) return "";
    const text = String(raw).replace(/\n/g, " ");
    let m = text.match(
      /(\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*(?:오전|오후)\s*\d{1,2}:\d{2})/,
    );
    if (m) return m[1];
    m = text.match(
      /(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2}|Z)?)/,
    );
    if (m) return m[0];
    return "";
  }

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
        if (!found.has(url))
          found.set(url, {
            id: url,
            title,
            subject: courseName,
            url,
            activity_type: activityType,
          });
      }
    }
    return [...found.values()];
  }

  async function fetchText(url, credentials) {
    const r = await fetch(url, {
      credentials: credentials || "include",
      cache: "no-store",
    });
    if (!r.ok) throw new Error(`${r.status} ${url}`);
    return r.text();
  }

  async function collectMyetlAssignments(options) {
    const opts = options || {};
    const delayMs = typeof opts.delayMs === "number" ? opts.delayMs : 350;
    const origin =
      typeof location !== "undefined" && location.origin
        ? location.origin
        : "https://myetl.snu.ac.kr";
    const myUrl = `${origin}/my/`;
    let myHtml;
    try {
      myHtml = await fetchText(myUrl, "include");
    } catch (e) {
      return { error: String(e), items: [] };
    }
    const courses = parseCoursesFromMyHtml(myHtml).slice(0, 50);
    const items = [];
    for (const c of courses) {
      await sleep(delayMs);
      let html;
      try {
        html = await fetchText(c.url, "include");
      } catch {
        continue;
      }
      const acts = parseActivitiesFromCourseHtml(html, c.name);
      for (const act of acts) {
        await sleep(delayMs);
        let body = "";
        try {
          body = await fetchText(act.url, "include");
        } catch {
          body = "";
        }
        const snippet = extractDeadlineSnippet(body);
        items.push({ ...act, deadline: snippet || "" });
      }
    }
    return { error: null, items };
  }

  const root =
    typeof globalThis !== "undefined" ? globalThis : typeof window !== "undefined" ? window : {};
  root.collectMyetlAssignments = collectMyetlAssignments;
})();
