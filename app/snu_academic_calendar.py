"""서울대학교 학사일정(2026학년도 기준)에 따른 수업·계절 학기 구간 (KST).

연도별 확장: academic_year Y = Y학년도(3월 시작) → 동일 월·일 패턴을 Y, Y+1에 적용.
방학 등 구간 밖 시각은 `pick_due_date_filter_window` 가 «다음» 학기 구간을 반환.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

SEOUL = timezone(timedelta(hours=9))


def _kst_start(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 0, 0, 0, tzinfo=SEOUL)


def _kst_end_inclusive(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 23, 59, 59, 999999, tzinfo=SEOUL)


def instructional_windows_for_year(academic_year: int) -> list[tuple[datetime, datetime]]:
    """academic_year: 학년도 표기(예: 2026 = 2026-03-03 시작 1학기).

    2026학년도 공식 + 보강 1주 반영(요청 스펙):
      1학기   Y-03-03 ~ Y-06-21
      여름    Y-06-23 ~ Y-08-03
      2학기   Y-09-01 ~ Y-12-21
      겨울    Y-12-22 ~ (Y+1)-01-25
    """
    y = academic_year
    return [
        (_kst_start(y, 3, 3), _kst_end_inclusive(y, 6, 21)),
        (_kst_start(y, 6, 23), _kst_end_inclusive(y, 8, 3)),
        (_kst_start(y, 9, 1), _kst_end_inclusive(y, 12, 21)),
        (_kst_start(y, 12, 22), _kst_end_inclusive(y + 1, 1, 25)),
    ]


def iter_instructional_windows(
    academic_year_min: int,
    academic_year_max: int,
) -> Iterator[tuple[datetime, datetime]]:
    for ay in range(academic_year_min, academic_year_max + 1):
        for w in instructional_windows_for_year(ay):
            yield w


def pick_due_date_filter_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """현재(KST)가 속한 학기 구간. 공백(방학)이면 가장 가까운 다음 구간.

    academic_year 후보는 now 기준 ±2년으로 충분.
    """
    if now is None:
        now = datetime.now(SEOUL)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=SEOUL)
    else:
        now = now.astimezone(SEOUL)

    y = now.year
    ay_min = max(2026, y - 1)
    ay_max = y + 2
    flat = sorted(iter_instructional_windows(ay_min, ay_max), key=lambda x: x[0])

    if not flat:
        return instructional_windows_for_year(2026)[0]

    for a, b in flat:
        if a <= now <= b:
            return (a, b)

    for a, b in flat:
        if now < a:
            return (a, b)

    return flat[-1]


def due_at_in_active_window(due_iso: str | None, *, now: datetime | None = None) -> bool:
    """Canvas due_at(ISO-8601)이 현재 정책 창 안에 있는지 (null 제외)."""
    if not due_iso or not str(due_iso).strip():
        return False
    raw = str(due_iso).strip().replace("Z", "+00:00")
    try:
        due = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if due.tzinfo is None:
        due = due.replace(tzinfo=SEOUL)
    else:
        due = due.astimezone(SEOUL)
    start, end = pick_due_date_filter_window(now)
    return start <= due <= end
