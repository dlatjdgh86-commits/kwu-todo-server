# KWU Todo Server

광운대 학생을 위한 AI 기반 TODO 생성 서버 — Python FastAPI 백엔드

## 실행 방법

### 1. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열고 OpenAI API 키를 입력합니다.

```
OPENAI_API_KEY=sk-...
```

> `.env` 파일은 **절대 GitHub에 커밋하지 마세요.**

### 2. 의존성 설치

```bash
pip install -r requirements.txt
```

### 3. 서버 실행

```bash
uvicorn server:app --reload
```

서버가 `http://localhost:8000` 에서 실행됩니다.
API 문서: `http://localhost:8000/docs`

## 프로젝트 구조

```
Python/
├── server.py            # FastAPI 앱 진입점
├── crawler.py           # 광운대 학사 일정 크롤링
├── llm_client.py        # OpenAI API 호출
├── todo_generator.py    # TODO 생성 로직
├── routes/
│   ├── todos.py         # /todos 엔드포인트
│   └── schedules.py     # /schedules 엔드포인트
├── requirements.txt
├── .env.example
└── .gitignore
```

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/todos/generate` | 크롤링 + LLM → TODO 자동 생성 |
| `GET` | `/todos` | 저장된 TODO 전체 조회 |
| `POST` | `/todos` | TODO 수동 추가 |
| `PUT` | `/todos/{id}` | TODO 수정 (완료 처리 등) |
| `DELETE` | `/todos/{id}` | TODO 삭제 |
| `GET` | `/schedules` | 학사 일정 조회 |

## TodoItem 스키마

```json
{
  "id": 1,
  "title": "알고리즘 중간고사 준비",
  "priority": "높음",
  "dueDate": "2025-05-08T00:00:00",
  "dDay": 3,
  "reason": "3일 후 시험, 즉시 시작 필요",
  "isCompleted": false,
  "isAIGenerated": true
}
```

**priority 허용 값:** `"높음"` / `"보통"` / `"낮음"`

## 역할 분담

| 팀원 | 담당 파일 |
|------|----------|
| 성호 | `crawler.py`, `llm_client.py`, `todo_generator.py` |
| 미혜 | `server.py`, `routes/todos.py`, `routes/schedules.py` |

## 브랜치 전략

```
main      ← 최종 제출본
develop   ← 통합 브랜치 (PR 대상)
feature/{이름}/{기능}
fix/{이름}/{내용}
```
