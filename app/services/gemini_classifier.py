"""
Claude Haiku로 Canvas 공지가 실제 시험 일정 공지인지 분류.
API 키 없거나 호출 실패 시 키워드 fallback 사용.
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error

_LOG = logging.getLogger(__name__)

_ENDPOINT = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-haiku-4-5-20251001"

_PROMPT = """다음 대학 강의 공지의 제목과 내용을 보고, 이것이 실제 시험(중간고사/기말고사) 날짜·일정을 안내하는 공지인지 판단하세요.

제목: {title}
내용: {body}

판단 기준:
- true: 시험이 언제 열리는지 날짜나 일정을 알리는 공지
- false: 시험 자료/문제/범위 공지, 발표 날짜 배정, 성적 공지, 수업 운영 안내 등

true 또는 false 중 하나만 답하세요."""

_FALLBACK_KEYWORDS = [
    "시험", "과제", "퀴즈", "exam", "assignment", "quiz",
    "deadline", "due", "midterm", "final",
]

# 동일 제목 반복 호출 방지 — 프로세스 내 메모리 캐시
_cache: dict[str, bool] = {}


def _keyword_fallback(title: str, body: str) -> bool:
    text = (title + " " + body).lower()
    return any(kw in text for kw in _FALLBACK_KEYWORDS)


def is_exam_schedule_announcement(
    title: str,
    body: str,
    api_key: str | None,
) -> bool:
    """
    Claude Haiku로 공지가 시험 일정 공지인지 판단.
    - api_key 없으면 키워드 fallback
    - API 오류 시 키워드 fallback
    """
    if not api_key:
        return _keyword_fallback(title, body)

    cache_key = f"{title[:100]}|{body[:200]}"
    if cache_key in _cache:
        _LOG.info("분류기 캐시 히트 → %s", title[:40])
        return _cache[cache_key]

    prompt = _PROMPT.format(
        title=(title or "").strip()[:200],
        body=(body or "").strip()[:400],
    )
    payload = json.dumps({
        "model": _MODEL,
        "max_tokens": 5,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        _ENDPOINT,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (
            data.get("content", [{}])[0]
            .get("text", "")
            .strip()
            .lower()
        )
        result = "true" in text
        _LOG.info("Claude 분류 [%s] → %s", title[:40], "YES" if result else "NO")
        _cache[cache_key] = result
        return result
    except urllib.error.HTTPError as exc:
        body_err = ""
        try:
            body_err = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        _LOG.warning("Claude API HTTP %s: %s | %s", exc.code, exc.reason, body_err)
        return _keyword_fallback(title, body)
    except Exception as exc:
        _LOG.warning("Claude API 오류: %s", exc)
        return _keyword_fallback(title, body)
