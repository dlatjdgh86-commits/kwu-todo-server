"""
광운대 학사 TODO 자동 생성 시스템 - FastAPI 로컬 서버
실행: uvicorn main:app --reload --port 8000
   또는 python main.py (WinForms subprocess 실행용)
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
from datetime import datetime

from routes.todo import router as todos_router          # 수정: routes.todos → routes.todo
from routes.schedules import router as schedules_router


# ────────────────────────────────────────────────
# 앱 생명주기
# ────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 서버 시작 - 학사 데이터 초기 로드 중...")
    # TODO: await crawler.run_initial_crawl()
    # TODO: db.init()
    yield
    print("🛑 서버 종료")


# ────────────────────────────────────────────────
# FastAPI 앱 초기화
# ────────────────────────────────────────────────

app = FastAPI(
    title="광운대 학사 TODO API",
    description="학사 일정 크롤링 + LLM 기반 TODO 자동 생성 로컬 서버",
    version="0.1.0",
    lifespan=lifespan,
)

# C# WinForms 앱에서 localhost로 호출하므로 CORS 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ────────────────────────────────────────────────
# 라우터 등록
# ────────────────────────────────────────────────

app.include_router(todos_router)       # /todos/*
app.include_router(schedules_router)   # /schedules/*


# ────────────────────────────────────────────────
# 헬스체크
# ────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ────────────────────────────────────────────────
# 직접 실행 (WinForms subprocess용)
# ────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )
