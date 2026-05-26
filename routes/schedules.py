"""
routes/schedules.py
학사일정 조회 + 크롤링 트리거 엔드포인트
"""

from fastapi import APIRouter, HTTPException, status, Query
from datetime import date
from typing import Optional, List
import uuid

from schemas import ScheduleResponse, MessageResponse
from database import fake_schedules

router = APIRouter(prefix="/schedules", tags=["학사일정"])


# ────────────────────────────────────────────────
# GET /schedules
# ────────────────────────────────────────────────

@router.get(
    "",
    response_model=List[ScheduleResponse],
    summary="학사일정 목록 조회",
    description="크롤링된 학사일정 목록을 반환합니다. refresh=true 시 재크롤링 후 반환합니다.",
)
def get_schedules(
    refresh: bool = Query(False, description="True 시 광운대 사이트 재크롤링"),
    category: Optional[str] = Query(None, description="카테고리 필터 (시험 / 수강신청 / 방학 / 행사 / 기타)"),
    from_date: Optional[date] = Query(None, description="시작일 이후 필터 (YYYY-MM-DD)"),
    to_date: Optional[date] = Query(None, description="시작일 이전 필터 (YYYY-MM-DD)"),
):
    if refresh:
        _run_crawl()

    schedules = list(fake_schedules)

    if category:
        schedules = [s for s in schedules if s["category"] == category]
    if from_date:
        schedules = [s for s in schedules if s["start_date"] >= from_date]
    if to_date:
        schedules = [s for s in schedules if s["start_date"] <= to_date]

    schedules.sort(key=lambda s: s["start_date"])
    return schedules


# ────────────────────────────────────────────────
# GET /schedules/{schedule_id}
# ────────────────────────────────────────────────

@router.get(
    "/{schedule_id}",
    response_model=ScheduleResponse,
    summary="특정 학사일정 단건 조회",
)
def get_schedule(schedule_id: str):
    target = next((s for s in fake_schedules if s["id"] == schedule_id), None)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"학사일정 ID '{schedule_id}'를 찾을 수 없습니다.",
        )
    return target


# ────────────────────────────────────────────────
# POST /schedules/crawl
# ────────────────────────────────────────────────

@router.post(
    "/crawl",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="학사일정 수동 크롤링 트리거",
)
def trigger_crawl():
    """
    C# WinForms의 '새로고침' 버튼 등에서 호출.
    ※ 현재는 더미 응답 반환 (크롤러 구현 후 교체 예정)
    """
    # TODO: from crawler import KwangwoonCrawler
    #       new_data = KwangwoonCrawler().run()
    #       fake_schedules.clear(); fake_schedules.extend(new_data)
    _run_crawl()
    return {"message": "학사일정 크롤링 완료 (갱신된 데이터는 GET /schedules 로 확인)"}


# ────────────────────────────────────────────────
# 내부 헬퍼 함수
# ────────────────────────────────────────────────

def _run_crawl():
    """실제 크롤러 연결 전까지 더미 데이터 유지"""
    print("🔄 [크롤러] 광운대 학사일정 크롤링 시작... (미구현 - 더미 데이터 유지)")

    dummy = {
        "id": f"sched-dummy-{uuid.uuid4().hex[:6]}",
        "title": "[샘플] 기말고사",
        "start_date": date(2026, 6, 15),
        "end_date": date(2026, 6, 19),
        "category": "시험",
        "source": "https://www.kw.ac.kr",
        "raw_text": "2026-1 기말고사 기간",
    }

    titles = [s["title"] for s in fake_schedules]
    if dummy["title"] not in titles:
        fake_schedules.append(dummy)
