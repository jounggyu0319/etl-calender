"""분류기 테스트 — python test_gemini.py <ANTHROPIC_API_KEY>"""
import sys
from app.services.gemini_classifier import classify_exam_announcement

api_key = sys.argv[1] if len(sys.argv) > 1 else None

cases = [
    # (title, body, 기대값, 설명)
    (
        "중간고사 일정 안내",
        "4월 23일 수요일 오후 2시에 중간고사가 진행됩니다. 강의실은 301호입니다.",
        True, "✅ 실제 시험 일정",
    ),
    (
        "기말고사 시험 안내",
        "기말고사는 6월 18일 오전 10시, 200호 강의실에서 실시됩니다.",
        True, "✅ 실제 시험 일정",
    ),
    (
        "중간고사 대비용 문제 올려드렸습니다",
        "시험 대비를 위한 연습문제와 기출문제를 업로드했습니다. 참고하세요.",
        False, "❌ 자료 공지 (오탐 방지)",
    ),
    (
        "발표 날짜 배정 안내",
        "팀 프로젝트 발표 날짜를 아래와 같이 배정합니다. 1팀: 5월 2일, 2팀: 5월 3일",
        False, "❌ 발표 날짜 배정 (오탐 방지)",
    ),
    (
        "중간고사 성적 공지",
        "중간고사 성적이 업로드되었습니다. 이의신청은 5월 10일까지입니다.",
        False, "❌ 성적 공지 (오탐 방지)",
    ),
    (
        "기말고사 범위 안내",
        "기말고사 범위는 10강부터 15강까지입니다. 교재 해당 챕터를 참고하세요.",
        False, "❌ 시험 범위 공지 (오탐 방지)",
    ),
    (
        "Midterm Exam Schedule",
        "The midterm exam will be held on April 25 at 3:00 PM in Room 401.",
        True, "✅ 영문 시험 일정",
    ),
    (
        "레포트 제출 안내",
        "중간 레포트 제출 마감은 4월 30일입니다. 이메일로 제출해주세요.",
        False, "❌ 레포트 제출 (오탐 방지)",
    ),
]

print(f"{'제목':<35} {'기대':>4} {'결과':>4} {'일치':>4}  설명")
print("-" * 80)

import time

correct = 0
for i, (title, body, expected, desc) in enumerate(cases):
    if i > 0:
        time.sleep(5)  # RPM 제한 방지
    result, exam_date = classify_exam_announcement(title, body, api_key)
    match = result == expected
    if match:
        correct += 1
    mark = "✅" if match else "❌"
    exp_str = "YES" if expected else "NO"
    res_str = "YES" if result else "NO"
    date_str = f" [{exam_date}]" if exam_date else ""
    print(f"{title[:33]:<35} {exp_str:>4} {res_str:>4}  {mark}  {desc}{date_str}")

print("-" * 80)
print(f"정확도: {correct}/{len(cases)} ({correct/len(cases)*100:.0f}%)")
