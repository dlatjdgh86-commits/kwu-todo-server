"""
database.py
인메모리 임시 DB — todos.py / schedules.py 가 함께 참조합니다.
SQLite 연동 시 이 파일만 교체하면 됩니다.
"""

from datetime import date

# ── TODO 저장소 ──────────────────────────────────
# { todo_id: dict } 형태
fake_todos: dict[str, dict] = {}

# ── 학사일정 저장소 ──────────────────────────────
# list[dict] 형태 (크롤러가 덮어씀)
fake_schedules: list[dict] = [
    # ── 과거 일정 ─────────────────────────────────
    {
        "id": "sched-001",
        "title": "2026학년도 1학기 중간고사",
        "start_date": date(2026, 4, 13),
        "end_date": date(2026, 4, 17),
        "category": "시험",
        "source": "https://www.kw.ac.kr",
        "raw_text": "2026학년도 1학기 중간고사 기간",
    },
    {
        "id": "sched-002",
        "title": "수강신청 (재수강/추가신청)",
        "start_date": date(2026, 3, 9),
        "end_date": date(2026, 3, 11),
        "category": "수강신청",
        "source": "https://www.kw.ac.kr",
        "raw_text": "2026-1 수강신청 재수강 및 추가신청",
    },
    # ── 현재/upcoming 일정 ─────────────────────────
    {
        "id": "sched-003",
        "title": "2026학년도 1학기 기말고사",
        "start_date": date(2026, 6, 15),
        "end_date": date(2026, 6, 19),
        "category": "시험",
        "source": "https://www.kw.ac.kr",
        "raw_text": "2026학년도 1학기 기말고사 기간",
    },
    {
        "id": "sched-004",
        "title": "성적 입력 마감",
        "start_date": date(2026, 6, 26),
        "end_date": date(2026, 6, 26),
        "category": "행사",
        "source": "https://www.kw.ac.kr",
        "raw_text": "2026-1 성적 입력 마감일",
    },
    {
        "id": "sched-005",
        "title": "2026학년도 1학기 하계방학 시작",
        "start_date": date(2026, 6, 27),
        "end_date": date(2026, 8, 28),
        "category": "방학",
        "source": "https://www.kw.ac.kr",
        "raw_text": "2026학년도 1학기 종강 및 하계방학",
    },
    {
        "id": "sched-006",
        "title": "2026학년도 2학기 수강신청",
        "start_date": date(2026, 7, 20),
        "end_date": date(2026, 7, 22),
        "category": "수강신청",
        "source": "https://www.kw.ac.kr",
        "raw_text": "2026학년도 2학기 수강신청",
    },
]
