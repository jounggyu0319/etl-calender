"""서울대 학기 창(방학 시 다음 학기) 단위 테스트."""

from __future__ import annotations

import unittest
from datetime import datetime

from app.snu_academic_calendar import (
    SEOUL,
    due_at_in_active_window,
    pick_due_date_filter_window,
)


class TestSnuAcademicCalendar(unittest.TestCase):
    def test_gap_between_spring_and_summer_uses_next_window(self) -> None:
        # 2026-06-22: 1학기 종료(6/21) 다음날, 여름 시작(6/23) 전 — 방학 공백
        dt = datetime(2026, 6, 22, 12, 0, 0, tzinfo=SEOUL)
        start, end = pick_due_date_filter_window(dt)
        self.assertEqual((start.month, start.day), (6, 23))
        self.assertEqual((end.month, end.day), (8, 3))

    def test_due_in_spring_accepted(self) -> None:
        n = datetime(2026, 4, 1, tzinfo=SEOUL)
        self.assertTrue(due_at_in_active_window("2026-04-15T12:00:00+09:00", now=n))

    def test_due_outside_active_window_rejected(self) -> None:
        n = datetime(2026, 4, 1, tzinfo=SEOUL)
        self.assertFalse(due_at_in_active_window("2026-02-10T12:00:00+09:00", now=n))


if __name__ == "__main__":
    unittest.main()
