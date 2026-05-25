"""
todo_generator.py
크롤링 데이터 + LLM을 활용한 TODO 자동 생성 및 우선순위 계산 모듈
"""

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from enum import IntEnum
from typing import Optional

from crawler import AcademicEvent, LMSAssignment, EverytimePost, CrawledData
from llm_client import BaseLLMClient, PromptTemplates, create_llm_client

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────

class Priority(IntEnum):
    CRITICAL = 1   # 긴급 (48시간 이내 마감 또는 매우 중요)
    HIGH     = 2   # 높음 (3~7일 이내)
    MEDIUM   = 3   # 보통 (1~2주)
    LOW      = 4   # 여유 (2주 이상)


PRIORITY_LABELS = {
    Priority.CRITICAL: "🔴 긴급",
    Priority.HIGH:     "🟠 높음",
    Priority.MEDIUM:   "🟡 보통",
    Priority.LOW:      "🟢 여유",
}


@dataclass
class TodoItem:
    id: str
    title: str
    description: str
    category: str
    priority: int                        # Priority 값 (1~4)
    priority_reason: str = ""
    due_date: Optional[str] = None       # YYYY-MM-DD
    estimated_hours: float = 1.0
    subtasks: list[str] = field(default_factory=list)
    source: str = ""
    completed: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def priority_label(self) -> str:
        return PRIORITY_LABELS.get(Priority(self.priority), "❓ 미정")

    @property
    def days_until_due(self) -> Optional[int]:
        if not self.due_date:
            return None
        try:
            due = datetime.strptime(self.due_date, "%Y-%m-%d").date()
            return (due - date.today()).days
        except ValueError:
            return None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["priority_label"] = self.priority_label
        d["days_until_due"] = self.days_until_due
        return d


@dataclass
class TodoList:
    items: list[TodoItem] = field(default_factory=list)
    summary: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def sorted_by_priority(self) -> list[TodoItem]:
        return sorted(
            self.items,
            key=lambda t: (
                t.priority,
                t.days_until_due if t.days_until_due is not None else 9999,
            )
        )

    def filter_by_priority(self, max_priority: int) -> list[TodoItem]:
        return [t for t in self.items if t.priority <= max_priority]

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "generated_at": self.generated_at,
            "total": len(self.items),
            "items": [t.to_dict() for t in self.sorted_by_priority()],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ──────────────────────────────────────────────
# 우선순위 계산 엔진 (규칙 기반)
# ──────────────────────────────────────────────

class PriorityCalculator:
    """
    마감일, 카테고리, 키워드를 기반으로 우선순위를 계산합니다.
    LLM 없이도 빠르게 우선순위를 결정할 수 있는 규칙 기반 엔진.
    """

    # 카테고리별 가중치 (낮을수록 중요)
    CATEGORY_WEIGHTS = {
        "시험": 0,
        "LMS과제": 1,
        "수강신청": 0,
        "등록": 0,
        "학사일정": 2,
        "에브리타임": 3,
        "기타": 3,
    }

    # 제목에 포함 시 우선순위 상향 키워드
    CRITICAL_KEYWORDS = ["중간고사", "기말고사", "수강신청", "마감", "긴급", "시험"]
    HIGH_KEYWORDS = ["과제", "제출", "퀴즈", "발표", "프로젝트", "레포트"]

    def calculate(self, title: str, category: str, due_date: Optional[str]) -> tuple[int, str]:
        """
        Returns:
            (priority: int, reason: str)
        """
        today = date.today()
        days_left: Optional[int] = None

        if due_date:
            try:
                d = datetime.strptime(due_date, "%Y-%m-%d").date()
                days_left = (d - today).days
            except ValueError:
                pass

        # ── 1. 마감일 기반 점수
        if days_left is not None:
            if days_left < 0:
                return Priority.CRITICAL, "이미 마감된 항목 (확인 필요)"
            elif days_left <= 2:
                base = Priority.CRITICAL
                reason = f"마감 {days_left}일 이내 (긴급)"
            elif days_left <= 7:
                base = Priority.HIGH
                reason = f"마감 {days_left}일 이내"
            elif days_left <= 14:
                base = Priority.MEDIUM
                reason = f"마감 {days_left}일 이내"
            else:
                base = Priority.LOW
                reason = f"마감 {days_left}일 후"
        else:
            base = Priority.MEDIUM
            reason = "마감일 미확인"

        # ── 2. 키워드 기반 상향 조정
        if any(kw in title for kw in self.CRITICAL_KEYWORDS):
            base = min(base, Priority.HIGH)  # 최소 HIGH
            reason += " + 중요 키워드 감지"

        if any(kw in title for kw in self.HIGH_KEYWORDS) and base > Priority.HIGH:
            base = Priority.HIGH
            reason += " + 과제 관련 키워드"

        # ── 3. 카테고리 가중치
        cat_weight = self.CATEGORY_WEIGHTS.get(category, 2)
        if cat_weight == 0 and base > Priority.HIGH:
            base = Priority.HIGH
            reason += f" + 중요 카테고리({category})"

        return int(base), reason


# ──────────────────────────────────────────────
# TODO 파서 (LLM 응답 → TodoItem 변환)
# ──────────────────────────────────────────────

class TodoParser:
    """LLM이 반환한 JSON을 TodoItem 리스트로 변환"""

    @staticmethod
    def parse(raw: dict | list) -> list[TodoItem]:
        if isinstance(raw, list):
            todos_data = raw
            summary = ""
        else:
            todos_data = raw.get("todos", [])
            summary = raw.get("summary", "")

        items = []
        for item in todos_data:
            try:
                todo = TodoItem(
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
                )
                items.append(todo)
            except Exception as e:
                logger.warning(f"TODO 파싱 실패: {e} | 원본: {item}")

        return items, summary


# ──────────────────────────────────────────────
# TODO 생성기 (메인 클래스)
# ──────────────────────────────────────────────

class TodoGenerator:
    """
    크롤링 데이터 → LLM → TODO 리스트 생성기

    동작 흐름:
    1. CrawledData를 프롬프트로 변환
    2. LLM에 전송하여 TODO JSON 생성
    3. 규칙 기반 우선순위로 검증/보완
    4. TodoList 반환
    """

    def __init__(self, llm_client: BaseLLMClient, use_rule_fallback: bool = True):
        """
        Args:
            llm_client: LLM API 클라이언트 (Claude 또는 OpenAI)
            use_rule_fallback: LLM 실패 시 규칙 기반으로 폴백할지 여부
        """
        self.llm = llm_client
        self.calculator = PriorityCalculator()
        self.use_rule_fallback = use_rule_fallback

    def generate(self, data: CrawledData) -> TodoList:
        """
        메인 진입점: 크롤링 데이터로부터 TODO 리스트 생성

        Args:
            data: crawler.py의 DataCollector가 반환한 CrawledData

        Returns:
            TodoList (우선순위 정렬 포함)
        """
        today_str = date.today().strftime("%Y년 %m월 %d일")
        logger.info(f"[TodoGenerator] TODO 생성 시작 ({today_str})")

        # ── 1단계: LLM으로 TODO 생성
        try:
            todos, summary = self._generate_with_llm(data, today_str)
        except Exception as e:
            logger.error(f"[TodoGenerator] LLM 생성 실패: {e}")
            if self.use_rule_fallback:
                logger.info("[TodoGenerator] 규칙 기반 폴백으로 전환")
                todos, summary = self._generate_with_rules(data)
            else:
                raise

        # ── 2단계: 우선순위 보완 (LLM 우선순위가 없거나 부정확한 경우 보정)
        todos = self._validate_and_fix_priorities(todos)

        # ── 3단계: 중복 제거
        todos = self._deduplicate(todos)

        result = TodoList(items=todos, summary=summary)
        logger.info(f"[TodoGenerator] 생성 완료: {len(todos)}개 TODO")
        return result

    def _generate_with_llm(
        self, data: CrawledData, today_str: str
    ) -> tuple[list[TodoItem], str]:
        prompt = PromptTemplates.build_todo_prompt(
            academic_events=data.academic_events,
            lms_assignments=data.lms_assignments,
            everytime_posts=data.everytime_posts,
            today_str=today_str,
        )
        logger.info("[TodoGenerator] LLM 호출 중...")
        raw = self.llm.chat_json(
            user_message=prompt,
            system_prompt=PromptTemplates.SYSTEM_TODO_GENERATOR,
            max_tokens=3000,
        )
        return TodoParser.parse(raw)

    def _generate_with_rules(
        self, data: CrawledData
    ) -> tuple[list[TodoItem], str]:
        """LLM 없이 규칙만으로 TODO 생성 (폴백)"""
        todos: list[TodoItem] = []

        # 학사 일정 → TODO
        for i, event in enumerate(data.academic_events):
            priority, reason = self.calculator.calculate(
                title=event.title,
                category=event.category,
                due_date=event.start_date.strftime("%Y-%m-%d"),
            )
            todos.append(TodoItem(
                id=f"acad_{i:03d}",
                title=f"[학사일정] {event.title} 준비",
                description=f"{event.category} 관련 학사 일정",
                category="학사일정",
                priority=priority,
                priority_reason=reason,
                due_date=event.start_date.strftime("%Y-%m-%d"),
                estimated_hours=1.0,
                subtasks=["일정 확인", "필요 서류 준비"],
                source="kwangwoon",
            ))

        # LMS 과제 → TODO
        for i, assignment in enumerate(data.lms_assignments):
            due_str = assignment.due_date.strftime("%Y-%m-%d") if assignment.due_date else None
            priority, reason = self.calculator.calculate(
                title=assignment.title,
                category="LMS과제",
                due_date=due_str,
            )
            todos.append(TodoItem(
                id=f"lms_{i:03d}",
                title=f"[{assignment.course_name}] {assignment.title}",
                description=f"{assignment.assignment_type} 완료 및 제출",
                category="LMS과제",
                priority=priority,
                priority_reason=reason,
                due_date=due_str,
                estimated_hours=2.0,
                subtasks=["문제 파악", "작성/풀이", "제출 확인"],
                source="lms",
            ))

        # 에브리타임 → TODO (시험 관련만)
        for i, post in enumerate(data.everytime_posts):
            if "시험" in post.keywords or "과제" in post.keywords:
                todos.append(TodoItem(
                    id=f"et_{i:03d}",
                    title=f"[에브리타임] {post.title}",
                    description=post.body[:200],
                    category="에브리타임",
                    priority=Priority.MEDIUM,
                    priority_reason="에브리타임 학사 관련 정보",
                    estimated_hours=0.5,
                    subtasks=["게시글 내용 확인", "필요 시 대응"],
                    source="everytime",
                ))

        summary = f"규칙 기반으로 {len(todos)}개 TODO 생성됨"
        return todos, summary

    def _validate_and_fix_priorities(self, todos: list[TodoItem]) -> list[TodoItem]:
        """
        LLM이 생성한 우선순위를 규칙 기반으로 검증하고 필요 시 보정
        - LLM이 너무 낮게 설정한 긴급 마감 항목을 상향
        """
        for todo in todos:
            rule_priority, rule_reason = self.calculator.calculate(
                title=todo.title,
                category=todo.category,
                due_date=todo.due_date,
            )
            # LLM 우선순위가 규칙보다 2단계 이상 낮으면 보정
            if todo.priority - rule_priority >= 2:
                logger.debug(
                    f"우선순위 보정: '{todo.title}' "
                    f"{todo.priority} → {rule_priority} ({rule_reason})"
                )
                todo.priority = rule_priority
                todo.priority_reason = f"[자동보정] {rule_reason}"

        return todos

    def _deduplicate(self, todos: list[TodoItem]) -> list[TodoItem]:
        """유사한 제목의 TODO 중복 제거"""
        seen_titles: set[str] = set()
        unique: list[TodoItem] = []
        for todo in todos:
            normalized = todo.title.strip().lower()
            if normalized not in seen_titles:
                seen_titles.add(normalized)
                unique.append(todo)
        return unique


# ──────────────────────────────────────────────
# 우선순위 재계산 (LLM 기반 심층 분석)
# ──────────────────────────────────────────────

class PriorityRefiner:
    """
    생성된 TODO 목록을 LLM으로 다시 검토하여 우선순위를 정밀하게 조정.
    TodoGenerator.generate() 이후 옵션으로 실행 가능.
    """

    def __init__(self, llm_client: BaseLLMClient):
        self.llm = llm_client

    def refine(self, todo_list: TodoList) -> TodoList:
        logger.info("[PriorityRefiner] LLM 기반 우선순위 재검토 중...")
        today_str = date.today().strftime("%Y년 %m월 %d일")
        todos_json = json.dumps(
            [t.to_dict() for t in todo_list.items],
            ensure_ascii=False, indent=2
        )
        prompt = PromptTemplates.build_priority_prompt(todos_json, today_str)

        try:
            raw = self.llm.chat_json(
                user_message=prompt,
                system_prompt=PromptTemplates.SYSTEM_TODO_GENERATOR,
                max_tokens=2000,
            )
            refined_items, _ = TodoParser.parse(raw)

            # 우선순위와 reason만 업데이트
            id_map = {t.id: t for t in refined_items}
            for item in todo_list.items:
                if item.id in id_map:
                    item.priority = id_map[item.id].priority
                    item.priority_reason = id_map[item.id].priority_reason

            logger.info("[PriorityRefiner] 우선순위 재검토 완료")
        except Exception as e:
            logger.warning(f"[PriorityRefiner] 재검토 실패 (원본 유지): {e}")

        return todo_list


# ──────────────────────────────────────────────
# 출력 포매터
# ──────────────────────────────────────────────

class TodoFormatter:
    """TodoList를 다양한 형식으로 출력"""

    @staticmethod
    def to_terminal(todo_list: TodoList) -> str:
        lines = [
            "=" * 60,
            f"📋 TODO 목록  |  생성: {todo_list.generated_at[:10]}",
            f"💡 {todo_list.summary}",
            "=" * 60,
        ]
        current_priority = None
        for todo in todo_list.sorted_by_priority():
            if todo.priority != current_priority:
                current_priority = todo.priority
                label = PRIORITY_LABELS.get(Priority(todo.priority), "")
                lines.append(f"\n{label}")
                lines.append("-" * 40)

            due = f"마감: {todo.due_date}" if todo.due_date else "마감 미정"
            days = f"({todo.days_until_due}일 후)" if todo.days_until_due is not None else ""
            lines.append(f"  ☐ {todo.title}")
            lines.append(f"    {due} {days}  |  예상 {todo.estimated_hours}h  |  {todo.category}")
            if todo.subtasks:
                for st in todo.subtasks:
                    lines.append(f"      - {st}")

        lines.append("=" * 60)
        return "\n".join(lines)

    @staticmethod
    def to_markdown(todo_list: TodoList) -> str:
        lines = [
            f"# 📋 TODO 목록",
            f"> 생성일: {todo_list.generated_at[:10]}  |  {todo_list.summary}",
            "",
        ]
        current_priority = None
        for todo in todo_list.sorted_by_priority():
            if todo.priority != current_priority:
                current_priority = todo.priority
                label = PRIORITY_LABELS.get(Priority(todo.priority), "")
                lines.append(f"\n## {label}\n")

            due = f"마감: `{todo.due_date}`" if todo.due_date else "마감 미정"
            lines.append(f"### ☐ {todo.title}")
            lines.append(f"- {due}  |  예상 소요: {todo.estimated_hours}시간  |  카테고리: `{todo.category}`")
            lines.append(f"- {todo.description}")
            if todo.subtasks:
                lines.append("- **세부 작업:**")
                for st in todo.subtasks:
                    lines.append(f"  - [ ] {st}")
            lines.append("")

        return "\n".join(lines)


# ──────────────────────────────────────────────
# 통합 파이프라인
# ──────────────────────────────────────────────

def run_pipeline(
    crawled_data: CrawledData,
    llm_provider: str = "claude",
    output_format: str = "terminal",
    refine_priorities: bool = False,
    save_json: str = None,
) -> TodoList:
    """
    크롤링 데이터 → TODO 생성까지 원스톱 실행

    Args:
        crawled_data: DataCollector.collect_all()의 반환값
        llm_provider: "claude" 또는 "openai"
        output_format: "terminal" | "markdown" | "json"
        refine_priorities: True이면 LLM 기반 우선순위 재검토 실행
        save_json: 파일 경로 지정 시 JSON 저장

    Returns:
        TodoList
    """
    client = create_llm_client(llm_provider)
    generator = TodoGenerator(client)
    todo_list = generator.generate(crawled_data)

    if refine_priorities:
        refiner = PriorityRefiner(client)
        todo_list = refiner.refine(todo_list)

    # 출력
    if output_format == "terminal":
        print(TodoFormatter.to_terminal(todo_list))
    elif output_format == "markdown":
        print(TodoFormatter.to_markdown(todo_list))
    elif output_format == "json":
        print(todo_list.to_json())

    # 저장
    if save_json:
        with open(save_json, "w", encoding="utf-8") as f:
            f.write(todo_list.to_json())
        logger.info(f"[Pipeline] TODO 저장 완료: {save_json}")

    return todo_list


# ──────────────────────────────────────────────
# 실행 예시
# ──────────────────────────────────────────────

if __name__ == "__main__":
    from crawler import DataCollector

    # 1. 데이터 수집
    collector = DataCollector(
        lms_username="학번",
        lms_password="비밀번호",
        everytime_username="에브리타임_아이디",
        everytime_password="에브리타임_비밀번호",
    )
    data = collector.collect_all()

    # 2. TODO 생성 및 출력
    todo_list = run_pipeline(
        crawled_data=data,
        llm_provider="claude",       # "openai"로 교체 가능
        output_format="terminal",
        refine_priorities=True,      # 우선순위 LLM 재검토
        save_json="todos.json",      # JSON 파일로 저장
    )
