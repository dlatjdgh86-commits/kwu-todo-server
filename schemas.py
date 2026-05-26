from pydantic import BaseModel, Field
from typing import Optional, Literal, List
from datetime import date, datetime
import uuid


# ────────────────────────────────────────────────
# TODO 스키마
# ────────────────────────────────────────────────

class TodoBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="TODO 제목")
    due_date: Optional[date] = Field(None, description="마감일")
    priority: Literal["high", "medium", "low"] = Field("medium", description="우선순위")
    category: Literal["학업", "행정", "장학", "기타"] = Field("기타", description="카테고리")
    source_event: Optional[str] = Field(None, description="근거가 된 학사일정 이름")


class TodoCreate(TodoBase):
    """POST /todos - 수동 TODO 생성 요청 바디"""
    pass


class TodoUpdate(BaseModel):
    """PUT /todos/{id} - TODO 수정 요청 바디 (모든 필드 선택)"""
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    due_date: Optional[date] = None
    priority: Optional[Literal["high", "medium", "low"]] = None
    category: Optional[Literal["학업", "행정", "장학", "기타"]] = None
    source_event: Optional[str] = None
    is_done: Optional[bool] = None


class TodoResponse(TodoBase):
    """TODO 응답 모델"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    is_done: bool = False
    created_at: datetime = Field(default_factory=datetime.now)

    model_config = {"from_attributes": True}


# ────────────────────────────────────────────────
# 학사일정 스키마
# ────────────────────────────────────────────────

class ScheduleResponse(BaseModel):
    """GET /schedules - 학사일정 응답 모델"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = Field(..., description="일정 제목")
    start_date: date = Field(..., description="시작일")
    end_date: Optional[date] = Field(None, description="종료일")
    category: Literal["시험", "수강신청", "방학", "행사", "기타"] = Field("기타")
    source: str = Field(..., description="데이터 출처 URL")
    raw_text: Optional[str] = Field(None, description="원본 텍스트")

    model_config = {"from_attributes": True}


# ────────────────────────────────────────────────
# AI TODO 생성 스키마
# ────────────────────────────────────────────────

class GenerateTodoRequest(BaseModel):
    """GET /todos/generate 쿼리 파라미터 대신 바디로 받을 경우 사용"""
    schedule_ids: Optional[List[str]] = Field(
        None, description="특정 학사일정 ID 목록 (없으면 전체 기반으로 생성)"
    )
    period_days: int = Field(30, ge=1, le=180, description="앞으로 며칠치 일정을 기반으로 생성할지")


class GenerateTodoResponse(BaseModel):
    """AI가 생성한 TODO 목록 응답 — C# TodoResponse와 매핑"""
    todos: List[TodoResponse]
    generated_count: int
    based_on_schedules: List[str] = Field(..., description="참고한 학사일정 제목 목록")


# ────────────────────────────────────────────────
# 공통 응답 스키마
# ────────────────────────────────────────────────

class MessageResponse(BaseModel):
    """단순 메시지 응답 (삭제 완료 등)"""
    message: str


class ErrorResponse(BaseModel):
    """에러 응답"""
    detail: str
    code: Optional[str] = None
