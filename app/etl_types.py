"""eTL 수집 결과 타입(Selenium 설치 없이 import 가능)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CollectResult:
    login_ok: bool = False
    login_note: str | None = None
    collect_failed_note: str | None = None
    courses_found: int = 0
    assign_links_found: int = 0
    quiz_links_found: int = 0
    announcement_keyword_hits: int = 0
    new_items: list[dict] = field(default_factory=list)
    updated_seen: set[str] = field(default_factory=set)
