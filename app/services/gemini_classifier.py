"""
Gemini 1.5 Flash (무료 티어)로 Canvas 공지가 실제 시험 날짜 공지인지 분류.
API 키 없거나 호출 실패 시 True 반환 (기존 키워드 필터 결과를 신뢰).
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error

_LOG = logging.getLogger(__name__)

_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent?key={key}"
)

_PROMPT = """다음 대학 강의 공지의 제목과 내용을 보고, 이것이 실제 시험(중간고사/기말고사) 날짜·일정을 안내하는 공지인지 판단하세요.

제목: {title}
내용: {body}

판단 기준:
- YES: 시험이 언제 열리는지 날짜나 일정을 알리는 공지
- NO: 시험 자료/문제/범위 공지, 발표 날짜 배정, 성적 공지, 수업 운영 안내 등

YES 또는 NO 중 하나만 답하세요."""


def is_exam_schedule_announcement(
    title: str,
    body: str,
    api_key: str | None,
) -> bool:
    """
    Gemini로 공지가 시험 일정 공지인지 판단.
    - api_key 없으면 True 반환 (키워드 필터 믿음)
    - API 오류 시 True 반환 (보수적: 놓치는 것보다 포함하는 게 나음)
    """
    if not api_key:
        return True

    prompt = _PROMPT.format(
        title=(title or "").strip()[:200],
        body=(body or "").strip()[:400],
    )
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 5, "temperature": 0},
    }).encode("utf-8")

    url = _ENDPOINT.format(key=api_key)
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(2):  # 429(rate limit) 시 1회 재시도
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
                .strip()
                .upper()
            )
            result = text.startswith("YES")
            _LOG.info("Gemini 분류 [%s] → %s", title[:40], "YES" if result else "NO")
            return result
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                # 인증 오류 — API 키 문제, 설정 확인 필요
                _LOG.error("Gemini API 인증 오류(401): GEMINI_API_KEY를 확인하세요.")
                return True
            if exc.code == 429 and attempt == 0:
                # 요청 초과 — 2초 후 재시도
                import time; time.sleep(2)
                continue
            _LOG.warning("Gemini API HTTP %s: %s", exc.code, exc.reason)
            return True
        except Exception as exc:
            _LOG.warning("Gemini API 오류: %s", exc)
            return True
    return True
