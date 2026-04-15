"""Google Calendar 일정 제목·메모 포맷(순수 문자열, Google API 의존 없음)."""

from __future__ import annotations

import re

__all__ = [
    "format_calendar_event_description",
    "format_calendar_event_summary",
    "normalize_course_display_name",
]


def normalize_course_display_name(subject: str) -> str:
    """과목 표시명: 'YYYY-N ' 접두 제거, 끝의 '(001)' 등 섹션 번호 제거."""
    s = (subject or "").strip()
    if not s:
        return "과목"
    s = re.sub(r"^\d{4}-\d+\s+", "", s)
    s = re.sub(r"\s*\(\d+\)\s*$", "", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s or "과목"


def _title_indicates_midterm(title: str) -> bool:
    t = title.lower()
    if "중간고사" in title:
        return True
    if "midterm" in t or "mid-term" in t or "mid term" in t:
        return True
    if re.search(r"\bmid\b", t):
        return True
    return False


def _title_indicates_final(title: str) -> bool:
    t = title.lower()
    if "기말고사" in title:
        return True
    if re.search(r"\bfinal\b", t):
        return True
    return False


def format_calendar_event_summary(assignment: dict) -> str:
    """Google Calendar 제목: 과목명_과제명 또는 과목명_중간고사|기말고사."""
    subj = normalize_course_display_name(str(assignment.get("subject") or ""))
    kind = str(assignment.get("activity_type") or "assign")
    if kind == "announcement_midterm":
        return f"{subj}_중간고사"
    if kind == "announcement_final":
        return f"{subj}_기말고사"
    title = str(assignment.get("title") or "").strip() or "(제목 없음)"
    if _title_indicates_midterm(title):
        return f"{subj}_중간고사"
    if _title_indicates_final(title):
        return f"{subj}_기말고사"
    return f"{subj}_{title}"


def _activity_kind_label(activity_type: str) -> str:
    k = activity_type or "assign"
    if k == "quiz":
        return "퀴즈"
    if k == "announcement_midterm":
        return "공지·중간고사"
    if k == "announcement_final":
        return "공지·기말고사"
    if k == "ical_feed":
        return "캘린더 구독(iCal)"
    if k == "forum_notice":
        return "공지·포럼"
    return "과제"


def format_calendar_event_description(assignment: dict) -> str:
    """Google Calendar 메모: 유형, 링크, 추가 설명."""
    kind = str(assignment.get("activity_type") or "assign")
    label = _activity_kind_label(kind)
    url = str(assignment.get("url") or "").strip()
    extra = (assignment.get("description_extra") or "").strip()
    raw_subj = str(assignment.get("subject") or "").strip()
    lines = [f"유형: {label}", ""]
    lines.append(f"링크: {url}" if url else "링크: (없음)")
    if raw_subj and raw_subj != normalize_course_display_name(raw_subj):
        lines.extend(["", f"원본 과목명: {raw_subj}"])
    if extra:
        lines.extend(["", extra])
    return "\n".join(lines)[:8000]
