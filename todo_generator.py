"""
todo_generator.py
크롤링 데이터 + LLM → TODO 자동 생성 모듈

흐름:
  CrawledData (crawler.py)
    → LLM 프롬프트 생성 (llm_client.PromptTemplates)
    → LLM 호출 (llm_client.BaseLLMClient)
    → JSON 파싱 → TodoItem 리스트
    → 규칙 기반 우선순위 보정
    → 중복 제거
    → TodoList 반환

main.py의 /todos/generate 엔드포인트에서 호출됩니다.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from enum import IntEnum
from typing import Optional, List, Tuple, Set, Union

from crawler import CrawledData
from llm_client import BaseLLMClient, PromptTemplates, create_llm_client

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 우선순위 레벨
# ──────────────────────────────────────────────

class Priority(IntEnum):
    CRITICAL = 1   # 긴급  (오늘~2일)
    HIGH     = 2   # 높음  (3~7일)
    MEDIUM   = 3   # 보통  (1~2주)
    LOW      = 4   # 여유  (2주 이상)


PRIORITY_LABELS = {
    Priority.CRITICAL: "🔴 긴급",
    Priority.HIGH:     "🟠 높음",
    Priority.MEDIUM:   "🟡 보통",
    Priority.LOW:      "🟢 여유",
}

# C# TodoItem.Priority 문자열과 매핑
PRIORITY_TO_STR = {
    Priority.CRITICAL: "high",
    Priority.HIGH:     "high",
    Priority.MEDIUM:   "medium",
    Priority.LOW:      "low",
}


# ──────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────

@dataclass
class TodoItem:
    """
    단일 TODO 항목
    C# ApiClient가 기대하는 필드명과 맞춥니다:
      id, title, priority(str), category, due_date, type, reason, source,
      is_completed, is_team_work, action_items
    """
    id: str
    title: str
    description: str
    category: str                        # 과제 / 퀴즈 / 온라인강의 / 시험 / 학사일정
    priority: int                        # Priority 값 (1~4)
    priority_reason: str = ""
    due_date: Optional[str] = None       # YYYY-MM-DD
    estimated_hours: float = 1.0
    subtasks: List[str] = field(default_factory=list)
    source: str = ""
    completed: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def priority_label(self) -> str:
        return PRIORITY_LABELS.get(Priority(self.priority), "❓")

    @property
    def priority_str(self) -> str:
        """C# 앱이 기대하는 문자열 우선순위"""
        return PRIORITY_TO_STR.get(Priority(self.priority), "medium")

    @property
    def days_until_due(self) -> Optional[int]:
        if not self.due_date:
            return None
        try:
            return (datetime.strptime(self.due_date, "%Y-%m-%d").date() - date.today()).days
        except ValueError:
            return None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["priority_label"]  = self.priority_label
        d["priority_str"]    = self.priority_str
        d["days_until_due"]  = self.days_until_due
        return d

    def to_api_dict(self) -> dict:
        """C# ApiClient가 기대하는 형식으로 직렬화"""
        return {
            "id":           self.id,
            "title":        self.title,
            "priority":     self.priority_str,
            "category":     self.category,
            "due_date":     self.due_date or "",
            "type":         self.category,
            "reason":       self.priority_reason,
            "source":       self.source,
            "is_completed": self.completed,
            "is_team_work": False,
            "action_items": self.subtasks,
        }


@dataclass
class TodoList:
    items: List[TodoItem] = field(default_factory=list)
    summary: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def sorted_by_priority(self) -> List[TodoItem]:
        return sorted(
            self.items,
            key=lambda t: (t.priority, t.days_until_due if t.days_until_due is not None else 9999)
        )

    def to_dict(self) -> dict:
        return {
            "summary":      self.summary,
            "generated_at": self.generated_at,
            "total":        len(self.items),
            "items":        [t.to_dict() for t in self.sorted_by_priority()],
        }

    def to_api_response(self) -> dict:
        """C# ApiClient.GetAIGeneratedTodosAsync()가 기대하는 TodoResponse 형식"""
        return {
            "todos":        [t.to_api_dict() for t in self.sorted_by_priority()],
            "summary":      self.summary,
            "generated_at": self.generated_at,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ──────────────────────────────────────────────
# 규칙 기반 우선순위 계산기
# ──────────────────────────────────────────────

class PriorityCalculator:
    """
    마감일 + 키워드 기반 우선순위 계산
    LLM 없이도 작동하는 폴백 엔진
    """

    CRITICAL_KEYWORDS = ["중간고사", "기말고사", "수강신청", "마감", "긴급", "시험", "미제출"]
    HIGH_KEYWORDS     = ["과제", "제출", "퀴즈", "발표", "프로젝트", "레포트", "온라인강의"]

    def calculate(self, title: str, category: str, due_date: Optional[str]) -> Tuple[int, str]:
        today     = date.today()
        days_left = None

        if due_date:
            try:
                days_left = (datetime.strptime(due_date, "%Y-%m-%d").date() - today).days
            except ValueError:
                pass

        # 마감일 기반
        if days_left is not None:
            if days_left < 0:
                base, reason = Priority.CRITICAL, "이미 마감 (확인 필요)"
            elif days_left <= 2:
                base, reason = Priority.CRITICAL, f"마감 {days_left}일 이내 (긴급)"
            elif days_left <= 7:
                base, reason = Priority.HIGH,     f"마감 {days_left}일 이내"
            elif days_left <= 14:
                base, reason = Priority.MEDIUM,   f"마감 {days_left}일 이내"
            else:
                base, reason = Priority.LOW,      f"마감 {days_left}일 후"
        else:
            base, reason = Priority.MEDIUM, "마감일 미확인"

        # 키워드 상향
        if any(kw in title for kw in self.CRITICAL_KEYWORDS):
            base    = min(base, Priority.HIGH)
            reason += " + 중요 키워드"
        if any(kw in title for kw in self.HIGH_KEYWORDS) and base > Priority.HIGH:
            base    = Priority.HIGH
            reason += " + 과제/강의 키워드"

        # 카테고리 상향
        if category in ("과제", "퀴즈", "온라인강의", "시험") and base > Priority.HIGH:
            base    = Priority.HIGH
            reason += f" + {category} 카테고리"

        return int(base), reason


# ──────────────────────────────────────────────
# LLM 응답 파서
# ──────────────────────────────────────────────

class TodoParser:
    @staticmethod
    def parse(raw: Union[dict, list]) -> Tuple[List[TodoItem], str]:
        todos_data = raw if isinstance(raw, list) else raw.get("todos", [])
        summary    = "" if isinstance(raw, list) else raw.get("summary", "")
        items = []
        for item in todos_data:
            try:
                items.append(TodoItem(
                    id=item.get("id") or f"todo_{uuid.uuid4().hex[:6]}",
                    title=item.get("title", "").strip(),
                    description=item.get("description", ""),
                    category=item.get("category", "기타"),
                    priority=int(item.get("priority", Priority.MEDIUM)),
                    priority_reason=item.get("priority_reason", ""),
                    due_date=item.get("due_date"),
                    estimated_hours=float(item.get("estimated_hours", 1.0)),
                    subtasks=item.get("subtasks", []),
                    source=item.get("source", ""),
                ))
            except Exception as e:
                logger.warning(f"TODO 파싱 실패: {e} | {item}")
        return items, summary


# ──────────────────────────────────────────────
# TODO 생성기
# ──────────────────────────────────────────────

class TodoGenerator:
    """
    CrawledData → LLM → TodoList

    main.py의 /todos/generate 엔드포인트에서 호출:
        generator = TodoGenerator(llm_client)
        todo_list = generator.generate(data)
        return todo_list.to_api_response()
    """

    def __init__(self, llm_client: BaseLLMClient, use_rule_fallback: bool = True):
        self.llm              = llm_client
        self.calculator       = PriorityCalculator()
        self.use_rule_fallback = use_rule_fallback

    def generate(self, data: CrawledData) -> TodoList:
        today_str = date.today().strftime("%Y년 %m월 %d일")
        logger.info(f"[TodoGenerator] 생성 시작 ({today_str})")

        # 1단계: LLM으로 TODO 생성
        try:
            todos, summary = self._generate_with_llm(data, today_str)
        except Exception as e:
            logger.error(f"[TodoGenerator] LLM 실패: {e}")
            if self.use_rule_fallback:
                logger.info("[TodoGenerator] 규칙 기반 폴백으로 전환")
                todos, summary = self._generate_with_rules(data)
            else:
                raise

        # 2단계: 우선순위 보정
        todos = self._fix_priorities(todos)

        # 3단계: 중복 제거
        todos = self._deduplicate(todos)

        logger.info(f"[TodoGenerator] 완료: {len(todos)}개")
        return TodoList(items=todos, summary=summary)

    def _generate_with_llm(self, data: CrawledData, today_str: str) -> Tuple[List[TodoItem], str]:
        prompt = PromptTemplates.build_prompt(data, today_str)
        logger.info("[TodoGenerator] LLM 호출 중...")
        raw = self.llm.chat_json(
            user_message=prompt,
            system_prompt=PromptTemplates.SYSTEM,
            max_tokens=3000,
        )
        return TodoParser.parse(raw)

    def _generate_with_rules(self, data: CrawledData) -> Tuple[List[TodoItem], str]:
        """LLM 없이 규칙만으로 TODO 생성 (폴백 / API 키 없을 때)"""
        todos: List[TodoItem] = []

        # 학사일정 → TODO
        for i, event in enumerate(data.academic_events):
            due_str = event.start_date.strftime("%Y-%m-%d")
            priority, reason = self.calculator.calculate(event.title, event.category, due_str)
            todos.append(TodoItem(
                id=f"acad_{i:03d}",
                title=f"[학사일정] {event.title} 확인",
                description=f"{event.category} 관련 일정",
                category="학사일정",
                priority=priority,
                priority_reason=reason,
                due_date=due_str,
                estimated_hours=0.5,
                subtasks=["일정 확인", "필요 준비사항 파악"],
                source="광운대 학사일정",
            ))

        # 과제/퀴즈 → TODO (미제출만)
        for i, a in enumerate(data.assignments):
            if a.is_submitted:
                continue
            due_str  = a.due_date.strftime("%Y-%m-%d") if a.due_date else None
            priority, reason = self.calculator.calculate(a.title, a.task_type, due_str)
            todos.append(TodoItem(
                id=f"assign_{i:03d}",
                title=f"[{a.task_type}] {a.course_name}: {a.title}",
                description=f"{a.task_type} 완료 및 제출 — {a.due_str}",
                category=a.task_type,
                priority=priority,
                priority_reason=reason,
                due_date=due_str,
                estimated_hours=2.0 if a.task_type == "과제" else 0.5,
                subtasks=["내용 파악", "작성/풀이", "제출 확인"],
                source=a.course_name,
            ))

        # 온라인강의 미수강 → TODO
        for i, lec in enumerate(data.lectures):
            due_str  = lec.deadline.strftime("%Y-%m-%d") if lec.deadline else None
            priority, reason = self.calculator.calculate(lec.title, "온라인강의", due_str)
            todos.append(TodoItem(
                id=f"lec_{i:03d}",
                title=f"[온라인강의] {lec.course_name} {lec.week}주차: {lec.title}",
                description=f"미수강 강의 — {lec.deadline_str}",
                category="온라인강의",
                priority=priority,
                priority_reason=reason,
                due_date=due_str,
                estimated_hours=1.0,
                subtasks=["강의 시청", "출석 확인"],
                source=lec.course_name,
            ))

        return todos, f"규칙 기반으로 {len(todos)}개 TODO 생성"

    def _fix_priorities(self, todos: List[TodoItem]) -> List[TodoItem]:
        """LLM 우선순위가 규칙보다 2단계 이상 낮으면 보정"""
        for todo in todos:
            rule_p, rule_r = self.calculator.calculate(
                todo.title, todo.category, todo.due_date
            )
            if todo.priority - rule_p >= 2:
                logger.debug(f"우선순위 보정: {todo.title} {todo.priority}→{rule_p}")
                todo.priority        = rule_p
                todo.priority_reason = f"[자동보정] {rule_r}"
        return todos

    def _deduplicate(self, todos: List[TodoItem]) -> List[TodoItem]:
        seen: Set[str] = set()
        unique = []
        for t in todos:
            key = t.title.strip().lower()
            if key not in seen:
                seen.add(key)
                unique.append(t)
        return unique


# ──────────────────────────────────────────────
# 우선순위 재검토 (선택 기능)
# ──────────────────────────────────────────────

class PriorityRefiner:
    """LLM으로 우선순위 재검토 — 선택적으로 사용"""

    def __init__(self, llm_client: BaseLLMClient):
        self.llm = llm_client

    def refine(self, todo_list: TodoList) -> TodoList:
        today_str  = date.today().strftime("%Y년 %m월 %d일")
        todos_json = json.dumps([t.to_dict() for t in todo_list.items], ensure_ascii=False, indent=2)
        prompt     = PromptTemplates.build_priority_prompt(todos_json, today_str)
        try:
            raw = self.llm.chat_json(
                user_message=prompt,
                system_prompt=PromptTemplates.SYSTEM,
                max_tokens=2000,
            )
            refined, _ = TodoParser.parse(raw)
            id_map = {t.id: t for t in refined}
            for item in todo_list.items:
                if item.id in id_map:
                    item.priority        = id_map[item.id].priority
                    item.priority_reason = id_map[item.id].priority_reason
        except Exception as e:
            logger.warning(f"[PriorityRefiner] 실패 (원본 유지): {e}")
        return todo_list


# ──────────────────────────────────────────────
# 통합 파이프라인 (main.py에서 호출)
# ──────────────────────────────────────────────

def run_pipeline(
    data: CrawledData,
    llm_provider: str = "claude",
    refine: bool = False,
) -> TodoList:
    """
    CrawledData → TodoList 원스톱 변환

    Args:
        data:         crawler.DataCollector.collect_all() 반환값
        llm_provider: "claude" 또는 "openai"
        refine:       True면 LLM으로 우선순위 재검토 (느림)

    Returns:
        TodoList (to_api_response()로 C# 앱에 반환)

    사용 예시 (main.py):
        data      = collector.collect_all()
        todo_list = run_pipeline(data, llm_provider="claude")
        return todo_list.to_api_response()
    """
    try:
        client    = create_llm_client(llm_provider)
        generator = TodoGenerator(client, use_rule_fallback=True)
    except Exception as e:
        logger.warning(f"[pipeline] LLM 클라이언트 생성 실패, 규칙 기반만 사용: {e}")
        generator = TodoGenerator.__new__(TodoGenerator)
        generator.llm               = None
        generator.calculator        = PriorityCalculator()
        generator.use_rule_fallback = True

        todos, summary = generator._generate_with_rules(data)
        todos = generator._fix_priorities(todos)
        todos = generator._deduplicate(todos)
        return TodoList(items=todos, summary=summary)

    todo_list = generator.generate(data)

    if refine:
        try:
            refiner   = PriorityRefiner(client)
            todo_list = refiner.refine(todo_list)
        except Exception as e:
            logger.warning(f"[pipeline] 우선순위 재검토 실패: {e}")

    return todo_list


# ──────────────────────────────────────────────
# [추가] LLM 우선순위 정렬 함수
# 기존 run_pipeline()은 TodoList 전체를 생성하지만
# 이 함수는 크롤링된 raw 항목을 LLM으로 정렬만 해서
# "[과제] 컴퓨터구조: Project (마감: 04/22)" 형식으로 출력합니다.
# ──────────────────────────────────────────────

def sort_by_llm(
    assignments: list,   # List[KlasAssignment]
    lectures: list,      # List[KlasLecture]
    llm_client: "BaseLLMClient",
) -> list:
    """
    KLAS 과제/퀴즈/온라인강의를 LLM으로 우선순위 정렬

    Args:
        assignments: crawler.KlasAssignment 리스트
        lectures:    crawler.KlasLecture 리스트
        llm_client:  llm_client.BaseLLMClient 인스턴스

    Returns:
        정렬된 항목 리스트 (KlasAssignment + KlasLecture 혼합)
    """
    import re as _re
    from datetime import date as _date

    # 전체 항목 합치기
    all_items = list(assignments) + list(lectures)
    if not all_items:
        return []

    today_str  = _date.today().strftime("%Y년 %m월 %d일")

    # LLM에 넘길 목록 문자열 생성
    lines = []
    for i, item in enumerate(all_items, 1):
        # KlasAssignment vs KlasLecture 구분
        if hasattr(item, "is_submitted"):
            status = "제출완료" if item.is_submitted else "미제출"
            due    = item.due_str or "마감일 미정"
            lines.append(f"{i}. [{item.task_type}] {item.course_name}: {item.title} (마감: {due}, {status})")
        else:
            week_title = f"{item.week}주차 {item.title}" if item.week else item.title
            lines.append(f"{i}. [온라인강의] {item.course_name}: {week_title} ({item.deadline_str}, 미수강)")

    task_list_str = "\n".join(lines)

    # LLM 호출 — 번호 배열만 반환받기
    try:
        prompt = PromptTemplates.build_sort_prompt(task_list_str, today_str)
        resp   = llm_client.chat(
            user_message=prompt,
            max_tokens=200,
            temperature=0.1,
        )
        content = resp.content.strip()

        # JSON 배열 파싱
        match = _re.search(r"\[[\d,\s]+\]", content)
        if match:
            order = json.loads(match.group())
            sorted_items = []
            used = set()
            for idx in order:
                if 1 <= idx <= len(all_items):
                    sorted_items.append(all_items[idx - 1])
                    used.add(idx)
            # 누락된 항목 뒤에 추가
            for i, item in enumerate(all_items, 1):
                if i not in used:
                    sorted_items.append(item)
            logger.info("[sort_by_llm] LLM 정렬 완료")
            return sorted_items

    except Exception as e:
        logger.warning(f"[sort_by_llm] LLM 정렬 실패, 마감일 순 폴백: {e}")

    # 폴백: 마감일 순 정렬
    def sort_key(item):
        submitted = getattr(item, "is_submitted", False)
        if hasattr(item, "due_date") and item.due_date:
            days = (item.due_date.date() - _date.today()).days
        elif hasattr(item, "deadline") and item.deadline:
            days = (item.deadline.date() - _date.today()).days
        else:
            days = 9999
        return (submitted, days)

    return sorted(all_items, key=sort_key)


# [추가] 정렬된 항목을 터미널에 출력하는 함수
# "[과제] 컴퓨터구조: Project (마감: 04/22)" 형식
def display_tasks(sorted_items: list) -> str:
    """
    정렬된 항목을 보기 좋게 출력

    Returns:
        출력 문자열 (print 가능)
    """
    from datetime import date as _date

    today_str = _date.today().strftime("%Y년 %m월 %d일")
    lines     = [
        f"\n{'='*60}",
        f"📋 오늘 할 일 ({today_str})",
        f"{'='*60}\n",
    ]

    for item in sorted_items:
        if hasattr(item, "display"):
            lines.append(item.display())
        else:
            lines.append(str(item))

    unfinished = sum(
        1 for item in sorted_items
        if not getattr(item, "is_submitted", False)
    )
    lines.append(f"\n{'='*60}")
    lines.append(f"미완료: {unfinished}개 / 전체: {len(sorted_items)}개")

    return "\n".join(lines)
