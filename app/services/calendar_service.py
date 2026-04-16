"""Google Calendar 일정 제목·메모 포맷(순수 문자열, Google API 의존 없음)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_SEOUL = timezone(timedelta(hours=9))

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
    """과제/공지 제목에서 시험 유형: 'midterm' | 'final' | 'general' | None.
    확장 프로그램 examKindFromTitle과 동일 기준 — "중간"/"기말" 단독 매칭 제거.
    """
    if not (title or "").strip():
        return None
    t = title.lower()
    orig = title
    if "중간고사" in orig:
        return "midterm"
    if "midterm" in t or "mid-term" in t or "mid term" in t:
        return "midterm"
    if "중간 시험" in orig or "중간시험" in orig or "중간 평가" in orig:
        return "midterm"
    if "기말고사" in orig:
        return "final"
    if "final exam" in t or "final test" in t or "final examination" in t:
        return "final"
    if "기말 시험" in orig or "기말시험" in orig or "기말 평가" in orig:
        return "final"
    if "시험" in orig:
        return "general"
    if re.search(r"\bexam\b", t):
        return "general"
    if re.search(r"\btest\b", t):
        return "general"
    return None


def announcement_title_matches_exam_keywords(title: str) -> bool:
    """Canvas 공지 수집용 — 확장 프로그램 announcementMatchesExamTitle과 동일 기준."""
    t = (title or "").strip()
    if not t:
        return False
    tl = t.lower()

    # 자료성·결과성·대체과제 공지 제외 키워드
    _EXCLUDE = [
        "대비용", "대비 문제", "기출문제", "기출 문제", "연습문제",
        "올려드렸", "자료 올",
        "성적", "결과", "레포트", "프로젝트", "project", "report",
        "발표 날짜", "날짜 배정", "발표일 배정", "수업 운영",
        # 시험 대체 과제·서평 공지
        "대체 과제", "대체과제", "서평", "take-home", "takehome",
        # 휴강 안내
        "휴강",
    ]
    has_exam_kw = bool(re.search(r"중간고사|기말고사|midterm|final exam", tl))
    excluded = any(k.lower() in tl for k in _EXCLUDE)
    # 시험 키워드 없이 강의 운영 안내만 → 제외
    is_ops_only = not has_exam_kw and bool(re.search(r"강의 운영|수업 운영", t))
    if excluded or is_ops_only:
        return False

    if classify_exam_kind_from_title(t):
        return True
    if re.search(r"\btest\b", tl):
        return True
    return any(x in t for x in ("시험 안내", "시험일정", "시험 일정", "시험일"))


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

    # assign·quiz: 실제 과제 제목 그대로 사용 (시험 키워드로 이름 변환 금지)
    # "중간고사 대체 서평과제 제출" → "정치학개론_중간고사 대체 서평과제 제출"
    return f"{subj}_{title}"


def _format_deadline_kr(deadline_str: str) -> str | None:
    """ISO 날짜/시각 문자열을 한국어 표기로 변환. 파싱 실패 시 None 반환."""
    s = (deadline_str or "").strip()
    if not s:
        return None
    try:
        if "T" in s:
            raw = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_SEOUL)
            dt = dt.astimezone(_SEOUL)
            h = dt.hour
            ampm = "오전" if h < 12 else "오후"
            h12 = h % 12 or 12
            return f"{dt.month}월 {dt.day}일 {ampm} {h12}:{dt.minute:02d}"
        elif re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            y, mo, d = int(s[:4]), int(s[5:7]), int(s[8:10])
            return f"{mo}월 {d}일"
        else:
            return None
    except Exception:
        return None


def format_calendar_event_description(assignment: dict) -> str:
    """메모: 과제면 '과제명 / 마감', 시험이면 '시험 종류 / 날짜 / 장소'."""
    kind = str(assignment.get("activity_type") or "assign")
    title = str(assignment.get("title") or "").strip()
    deadline = str(assignment.get("deadline") or "").strip()

    if _is_exam_activity(assignment):
        # 시험 종류
        if kind == "announcement_midterm":
            exam_label = "중간고사"
        elif kind == "announcement_final":
            exam_label = "기말고사"
        else:
            ek = classify_exam_kind_from_title(title)
            exam_label = {"midterm": "중간고사", "final": "기말고사"}.get(ek or "", "시험")

        lines = [exam_label]
        date_str = _format_deadline_kr(deadline)
        if date_str:
            lines.append(f"날짜: {date_str}")
        location = str(assignment.get("exam_location") or "").strip()
        if location:
            lines.append(f"장소: {location}")
        return "\n".join(lines)[:8000]

    # 과제 / 퀴즈
    lines = []
    if title:
        lines.append(title)
    date_str = _format_deadline_kr(deadline)
    if date_str:
        lines.append(f"마감: {date_str}")
    return "\n".join(lines)[:8000]
