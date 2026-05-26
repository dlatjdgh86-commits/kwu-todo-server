"""
database.py
데이터 영구 저장 모듈

- TODO: SQLite 파일 (kwu_todo.db) — 서버 재시작 후에도 유지
- 학사일정: 인메모리 (크롤링 캐시, 매 크롤링 시 갱신)

fake_todos 는 dict-like 인 TodoStore 로 교체되었습니다.
routes/todo.py 의 코드 변경 없이 동일하게 동작합니다.
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime, date

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# SQLite 경로
# ──────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent / "kwu_todo.db"


# ──────────────────────────────────────────────────────────────
# DB 초기화
# ──────────────────────────────────────────────────────────────

def _init_db() -> None:
    """todos 테이블이 없으면 생성합니다."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id           TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                due_date     TEXT,
                priority     TEXT DEFAULT 'medium',
                category     TEXT DEFAULT '기타',
                source_event TEXT,
                is_done      INTEGER DEFAULT 0,
                created_at   TEXT NOT NULL
            )
        """)
        conn.commit()
    logger.info(f"[DB] SQLite 초기화 완료: {DB_PATH}")


# ──────────────────────────────────────────────────────────────
# Row → dict 변환 헬퍼
# ──────────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    # due_date: "YYYY-MM-DD" 문자열 → date 객체 (Pydantic TodoResponse 검증용)
    if d.get("due_date"):
        try:
            d["due_date"] = date.fromisoformat(d["due_date"])
        except (ValueError, TypeError):
            d["due_date"] = None
    else:
        d["due_date"] = None

    # is_done: SQLite INTEGER(0/1) → Python bool
    d["is_done"] = bool(d.get("is_done", 0))

    # created_at: 문자열 → datetime
    if d.get("created_at"):
        try:
            d["created_at"] = datetime.fromisoformat(d["created_at"])
        except (ValueError, TypeError):
            d["created_at"] = datetime.now()
    else:
        d["created_at"] = datetime.now()

    return d


# ──────────────────────────────────────────────────────────────
# TodoStore — dict-like SQLite 래퍼
#
# routes/todo.py 가 fake_todos 를 dict 처럼 사용하므로
# __setitem__, __getitem__, __delitem__, __contains__, values()
# 인터페이스를 그대로 유지합니다.
# ──────────────────────────────────────────────────────────────

class TodoStore:
    """
    SQLite 를 dict 처럼 사용할 수 있는 래퍼.
    fake_todos[id] = {...}  → INSERT OR REPLACE
    del fake_todos[id]      → DELETE
    fake_todos.values()     → 전체 SELECT
    id in fake_todos        → SELECT COUNT
    """

    def __init__(self):
        _init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    # ── 읽기 ──────────────────────────────────────────────────

    def __contains__(self, todo_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM todos WHERE id = ?", (todo_id,)
            ).fetchone()
            return row is not None

    def __getitem__(self, todo_id: str) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM todos WHERE id = ?", (todo_id,)
            ).fetchone()
            if row is None:
                raise KeyError(todo_id)
            return _row_to_dict(row)

    def values(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM todos ORDER BY created_at DESC"
            ).fetchall()
            return [_row_to_dict(r) for r in rows]

    def keys(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT id FROM todos").fetchall()
            return [r["id"] for r in rows]

    # ── 쓰기 ──────────────────────────────────────────────────

    def __setitem__(self, todo_id: str, data: dict) -> None:
        """INSERT OR REPLACE (Upsert)"""
        # due_date: date/datetime 객체 → ISO 문자열
        due = data.get("due_date")
        if isinstance(due, (date, datetime)):
            due = due.isoformat()
        elif due is not None:
            due = str(due)

        # created_at: datetime → ISO 문자열
        created = data.get("created_at")
        if isinstance(created, datetime):
            created = created.isoformat()
        elif created is None:
            created = datetime.now().isoformat()
        else:
            created = str(created)

        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO todos
                    (id, title, due_date, priority, category, source_event, is_done, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(todo_id),
                data.get("title", ""),
                due,
                data.get("priority", "medium"),
                data.get("category", "기타"),
                data.get("source_event"),
                1 if data.get("is_done") else 0,
                created,
            ))
            conn.commit()

    def __delitem__(self, todo_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
            conn.commit()


# ──────────────────────────────────────────────────────────────
# 학사일정 (인메모리 캐시 — 크롤링 후 갱신)
# ──────────────────────────────────────────────────────────────

fake_schedules: list[dict] = [
    # ── 과거 일정 ─────────────────────────────────────────────
    {
        "id": "sched-001",
        "title": "2026학년도 1학기 중간고사",
        "start_date": date(2026, 4, 13),
        "end_date":   date(2026, 4, 17),
        "category": "시험",
        "source": "https://www.kw.ac.kr",
        "raw_text": "2026학년도 1학기 중간고사 기간",
    },
    {
        "id": "sched-002",
        "title": "수강신청 (재수강/추가신청)",
        "start_date": date(2026, 3, 9),
        "end_date":   date(2026, 3, 11),
        "category": "수강신청",
        "source": "https://www.kw.ac.kr",
        "raw_text": "2026-1 수강신청 재수강 및 추가신청",
    },
    # ── 현재/upcoming 일정 ────────────────────────────────────
    {
        "id": "sched-003",
        "title": "2026학년도 1학기 기말고사",
        "start_date": date(2026, 6, 15),
        "end_date":   date(2026, 6, 19),
        "category": "시험",
        "source": "https://www.kw.ac.kr",
        "raw_text": "2026학년도 1학기 기말고사 기간",
    },
    {
        "id": "sched-004",
        "title": "성적 입력 마감",
        "start_date": date(2026, 6, 26),
        "end_date":   date(2026, 6, 26),
        "category": "행사",
        "source": "https://www.kw.ac.kr",
        "raw_text": "2026-1 성적 입력 마감일",
    },
    {
        "id": "sched-005",
        "title": "2026학년도 1학기 하계방학 시작",
        "start_date": date(2026, 6, 27),
        "end_date":   date(2026, 8, 28),
        "category": "방학",
        "source": "https://www.kw.ac.kr",
        "raw_text": "2026학년도 1학기 종강 및 하계방학",
    },
    {
        "id": "sched-006",
        "title": "2026학년도 2학기 수강신청",
        "start_date": date(2026, 7, 20),
        "end_date":   date(2026, 7, 22),
        "category": "수강신청",
        "source": "https://www.kw.ac.kr",
        "raw_text": "2026학년도 2학기 수강신청",
    },
]

# ──────────────────────────────────────────────────────────────
# 모듈 임포트 시 즉시 초기화 (fake_todos → TodoStore)
# ──────────────────────────────────────────────────────────────

fake_todos = TodoStore()
