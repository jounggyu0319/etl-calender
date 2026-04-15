"""Google Calendar 일정 제목·메모 포맷(순수 문자열, Google API 의존 없음)."""

from __future__ import annotations

import re

__all__ = [
    "classify_exam_kind_from_title",
    "format_calendar_event_description",
    "format_calendar_event_summary",
    "normalize_course_display_name",
    "announcement_title_matches_exam_keywords",
]


def normalize_course_display_name(subject: str) -> str:
    """과목 표시명: YYYY-N 접두 전역 제거, (001) 섹션 제거, 구문 중복 정리."""
    s = (subject or "").strip()
    if not s:
        return "과목"
    s = s.strip("[]").strip()
    s = re.sub(r"\d{4}-\d+\s+", "", s)
    s = re.sub(r"\s*\(\d+\)\s*", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # 전체 구문이 반복된 경우 제거: "ABC XYZ ABC XYZ" → "ABC XYZ"
    parts = s.split()
    n = len(parts)
    for half in range(1, n // 2 + 1):
        if n % half == 0 and parts[:half] * (n // half) == parts:
            s = " ".join(parts[:half])
            break
    return s or "과목"


def classify_exam_kind_from_title(title: str) -> str | None:
    """과제/공지 제목에서 시험 유형: 'midterm' | 'final' | 'general' | None (우선순위: 중간→기말→일반)."""
    if not (title or "").strip():
        return None
    t = title.lower()
    orig = title
    if "중간고사" in orig:
        return "midterm"
    if "midterm" in t or "mid-term" in t or "mid term" in t:
        return "midterm"
    if "중간" in orig:
        return "midterm"
    if "기말고사" in orig:
        return "final"
    if "final exam" in t:
        return "final"
    if "기말" in orig:
        return "final"
    if re.search(r"\bfinal\b", t):
        return "final"
    if "시험" in orig:
        return "general"
    if re.search(r"\bexam\b", t):
        return "general"
    if re.search(r"\btest\b", t):
        return "general"
    return None


def announcement_title_matches_exam_keywords(title: str) -> bool:
    """Canvas 공지 수집용: 시험 관련 키워드가 제목에 있으면 True."""
    if not (title or "").strip():
        return False
    if classify_exam_kind_from_title(title):
        return True
    t = (title or "").lower()
    if re.search(r"\btest\b", t):
        return True
    return any(
        x in title
        for x in ("시험 안내", "시험일정", "시험 일정", "시험일")
    )


def _is_exam_activity(assignment: dict) -> bool:
    kind = str(assignment.get("activity_type") or "assign")
    if kind in ("exam", "announcement_midterm", "announcement_final"):
        return True
    if kind in ("assign", "quiz"):
        return classify_exam_kind_from_title(str(assignment.get("title") or "")) is not None
    return False


def format_calendar_event_summary(assignment: dict) -> str:
    """Google Calendar 제목: 과목명_과제명 또는 과목명_중간고사|기말고사|시험."""
    subj = normalize_course_display_name(str(assignment.get("subject") or ""))
    kind = str(assignment.get("activity_type") or "assign")

    if kind == "announcement_midterm":
        return f"{subj}_중간고사"
    if kind == "announcement_final":
        return f"{subj}_기말고사"

    title = str(assignment.get("title") or "").strip() or "(제목 없음)"

    if kind == "exam":
        ek = classify_exam_kind_from_title(title)
        if ek == "midterm":
            return f"{subj}_중간고사"
        if ek == "final":
            return f"{subj}_기말고사"
        if ek == "general":
            return f"{subj}_시험"
        return f"{subj}_시험"

    ek = classify_exam_kind_from_title(title)
    if ek == "midterm":
        return f"{subj}_중간고사"
    if ek == "final":
        return f"{subj}_기말고사"
    if ek == "general":
        return f"{subj}_시험"

    return f"{subj}_{title}"


def format_calendar_event_description(assignment: dict) -> str:
    """메모: eTL 과제 또는 eTL 시험 + 링크 + 추가 설명."""
    url = str(assignment.get("url") or "").strip()
    extra = (assignment.get("description_extra") or "").strip()
    head = "eTL 시험" if _is_exam_activity(assignment) else "eTL 과제"
    lines = [head, f"링크: {url}" if url else "링크: (없음)"]
    if extra:
        lines.extend(["", extra])
    return "\n".join(lines)[:8000]
