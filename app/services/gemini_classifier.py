"""
Claude Haiku로 Canvas 공지가 실제 시험 일정 공지인지 분류하고 날짜를 추출.
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

_PROMPT = """다음 대학 강의 공지의 제목과 내용을 보고 판단하세요.

제목: {title}
내용: {body}

아래 JSON 형식으로만 답하세요 (다른 텍스트 없이):
{{"is_exam": true 또는 false, "exam_date": "YYYY-MM-DD" 또는 null}}

판단 기준 (한국어·영어 공지 모두 적용):
- is_exam=true: 시험/exam이 언제(날짜·시간·장소) 열리는지 알리는 공지
  예) "중간고사는 4월 24일 오후 2시에 진행됩니다" / "The final exam is on June 18 at 10AM"
  ★ 제목이 '강의 운영 안내'처럼 보여도 본문에 시험 날짜·시간·고사장이 구체적으로 명시되어 있으면 is_exam=true
  ★ "N.M(예)" 또는 "N월 M일" 형태의 날짜 + 시험 시간표가 본문에 있으면 is_exam=true
- is_exam=false (반드시 제외):
  * 대체 과제/서평/take-home essay/레포트로 시험을 대신하는 공지
  * 시험 자료/기출/범위/study guide/준비물 공지 (시험 자체 일정 없음)
  * 성적/결과/grade released 공지
  * 발표 날짜 배정, 수업 운영 공지 (시험 날짜 언급 없음)
- exam_date: is_exam=true일 때 시험 날짜(YYYY-MM-DD, 한국 기준 연도 사용). 날짜가 없으면 null. is_exam=false면 null.
  ★ "4.24(예)" → 2026-04-24, "4월 24일" → 2026-04-24 형식으로 변환"""

_FALLBACK_KEYWORDS = [
    "중간고사", "기말고사", "시험", "과제", "퀴즈",
    "exam", "assignment", "quiz", "deadline", "due", "midterm", "final",
]

# 동일 제목 반복 호출 방지 — 프로세스 내 메모리 캐시
_cache: dict[str, tuple[bool, str | None]] = {}


def _keyword_fallback(title: str, body: str) -> tuple[bool, str | None]:
    text = (title + " " + body).lower()
    return any(kw in text for kw in _FALLBACK_KEYWORDS), None


def classify_exam_announcement(
    title: str,
    body: str,
    api_key: str | None,
) -> tuple[bool, str | None]:
    """
    Claude Haiku로 공지가 시험 일정 공지인지 판단하고 날짜 추출.
    반환: (is_exam, exam_date_iso_or_None)
    - api_key 없으면 키워드 fallback
    - API 오류 시 키워드 fallback
    """
    if not api_key:
        return _keyword_fallback(title, body)

    cache_key = f"v2|{title[:100]}|{body[:400]}"
    if cache_key in _cache:
        _LOG.info("분류기 캐시 히트 → %s", title[:40])
        return _cache[cache_key]

    prompt = _PROMPT.format(
        title=(title or "").strip()[:200],
        body=(body or "").strip()[:1200],
    )
    payload = json.dumps({
        "model": _MODEL,
        "max_tokens": 60,
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
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        raw = (
            data.get("content", [{}])[0]
            .get("text", "")
            .strip()
        )
        # 마크다운 코드블록 제거 (```json ... ``` 또는 ``` ... ```)
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        is_exam = bool(parsed.get("is_exam", False))
        exam_date = parsed.get("exam_date") or None
        if exam_date and not isinstance(exam_date, str):
            exam_date = None
        result = (is_exam, exam_date)
        _LOG.info("Claude 분류 [%s] → is_exam=%s date=%s", title[:40], is_exam, exam_date)
        _cache[cache_key] = result
        return result
    except (json.JSONDecodeError, KeyError, TypeError):
        # JSON 파싱 실패 시 fallback
        _LOG.warning("Claude 응답 파싱 실패 → fallback: %s", title[:40])
        return _keyword_fallback(title, body)
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


# 하위 호환 — 기존 코드가 bool만 필요할 때
def is_exam_schedule_announcement(
    title: str,
    body: str,
    api_key: str | None,
) -> bool:
    is_exam, _ = classify_exam_announcement(title, body, api_key)
    return is_exam
