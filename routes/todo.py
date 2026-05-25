"""
routes/todo.py
TODO CRUD + AI 자동 생성 엔드포인트
"""

from fastapi import APIRouter, HTTPException, status, Query
from datetime import datetime, date, timedelta
from typing import Optional
import uuid

from schemas import (
    TodoCreate,
    TodoUpdate,
    TodoResponse,
    GenerateTodoResponse,
    MessageResponse,
)
from database import fake_todos, fake_schedules

router = APIRouter(prefix="/todos", tags=["TODO"])


# ────────────────────────────────────────────────
# GET /todos/generate  ← /todos/{id} 보다 먼저 선언해야 충돌 없음
# ────────────────────────────────────────────────

@router.get(
    "/generate",
    response_model=GenerateTodoResponse,
    summary="AI TODO 자동 생성",
    description="저장된 학사일정을 기반으로 LLM이 TODO를 생성하여 반환합니다.",
)
def generate_todos(
    period_days: int = Query(30, ge=1, le=180, description="오늘부터 며칠치 일정을 참고할지"),
):
    """
    1. 오늘 ~ (오늘 + period_days) 범위의 학사일정 필터링
    2. 일정 텍스트를 LLM에 전달하여 TODO 생성 요청
    3. 생성된 TODO 목록 반환

    ※ 현재는 샘플 데이터 반환 (LLM 연동 후 교체 예정)
    """
    today = date.today()
    deadline = today + timedelta(days=period_days)

    # 기간 내 학사일정 필터링
    target_schedules = [
        s for s in fake_schedules
        if s["start_date"] <= deadline
    ]

    if not target_schedules:
        return GenerateTodoResponse(
            todos=[],
            generated_count=0,
            based_on_schedules=[],
        )

    # ── LLM 연동 전 임시 로직: 일정 1개당 TODO 1개 생성 ──
    # TODO: 아래 블록을 LLM API 호출로 교체
    #   prompt = build_prompt(target_schedules)
    #   llm_response = call_llm(prompt)
    #   generated = parse_llm_response(llm_response)
    generated_todos: list[TodoResponse] = []
    for sched in target_schedules:
        todo = TodoResponse(
            id=str(uuid.uuid4()),
            title=f"{sched['title']} 준비하기",
            due_date=sched["start_date"] - timedelta(days=2),
            priority=_priority_from_category(sched["category"]),
            category=_map_category(sched["category"]),
            source_event=sched["title"],
            is_done=False,
            created_at=datetime.now(),
        )
        generated_todos.append(todo)

    return GenerateTodoResponse(
        todos=generated_todos,
        generated_count=len(generated_todos),
        based_on_schedules=[s["title"] for s in target_schedules],
    )


# ────────────────────────────────────────────────
# GET /todos
# ────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[TodoResponse],
    summary="TODO 전체 조회",
)
def get_todos(
    is_done: Optional[bool] = Query(None, description="완료 여부 필터"),
    priority: Optional[str] = Query(None, description="우선순위 필터 (high / medium / low)"),
    category: Optional[str] = Query(None, description="카테고리 필터"),
):
    todos = list(fake_todos.values())

    if is_done is not None:
        todos = [t for t in todos if t["is_done"] == is_done]
    if priority:
        todos = [t for t in todos if t["priority"] == priority]
    if category:
        todos = [t for t in todos if t["category"] == category]

    todos.sort(key=lambda t: (t["due_date"] is None, t["due_date"]))
    return todos


# ────────────────────────────────────────────────
# POST /todos
# ────────────────────────────────────────────────

@router.post(
    "",
    response_model=TodoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="TODO 수동 추가",
)
def create_todo(todo: TodoCreate):
    new_todo = TodoResponse(
        id=str(uuid.uuid4()),
        **todo.model_dump(),
        is_done=False,
        created_at=datetime.now(),
    )
    fake_todos[new_todo.id] = new_todo.model_dump()
    return new_todo


# ────────────────────────────────────────────────
# PUT /todos/{todo_id}
# ────────────────────────────────────────────────

@router.put(
    "/{todo_id}",
    response_model=TodoResponse,
    summary="TODO 수정 (완료 체크 포함)",
)
def update_todo(todo_id: str, update: TodoUpdate):
    if todo_id not in fake_todos:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"TODO ID '{todo_id}'를 찾을 수 없습니다.",
        )
    stored = fake_todos[todo_id]
    update_data = update.model_dump(exclude_unset=True)
    stored.update(update_data)
    fake_todos[todo_id] = stored
    return stored


# ────────────────────────────────────────────────
# DELETE /todos/{todo_id}
# ────────────────────────────────────────────────

@router.delete(
    "/{todo_id}",
    response_model=MessageResponse,
    summary="TODO 삭제",
)
def delete_todo(todo_id: str):
    if todo_id not in fake_todos:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"TODO ID '{todo_id}'를 찾을 수 없습니다.",
        )
    del fake_todos[todo_id]
    return {"message": f"TODO({todo_id}) 삭제 완료"}


# ────────────────────────────────────────────────
# 내부 헬퍼 함수
# ────────────────────────────────────────────────

def _priority_from_category(schedule_category: str) -> str:
    mapping = {"시험": "high", "수강신청": "high", "행사": "medium", "방학": "low", "기타": "medium"}
    return mapping.get(schedule_category, "medium")


def _map_category(schedule_category: str) -> str:
    mapping = {"시험": "학업", "수강신청": "행정", "장학": "장학", "행사": "기타", "방학": "기타", "기타": "기타"}
    return mapping.get(schedule_category, "기타")
