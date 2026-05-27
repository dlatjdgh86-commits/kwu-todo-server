# KWU Todo Server — Agent Context

## 프로젝트 개요

광운대 학사 일정을 크롤링하고 OpenAI LLM으로 TODO를 자동 생성하는 FastAPI 백엔드.
C# WinForms 클라이언트(kwu-todo-client)와 HTTP로 통신한다.

- **서버 주소**: `http://localhost:8000`
- **Python 버전**: 3.8 이상 (3.8 호환성 유지 필수)
- **GitHub**: `https://github.com/neo-mwo-dawe/kwu-todo-server`
- **작업 브랜치**: `develop`

---

## 아키텍처

```
main.py                  # FastAPI 앱 진입점, lifespan으로 startup 처리
crawler.py               # 광운대 학사 일정 크롤링 (AcademicEvent 반환)
llm_client.py            # OpenAI API 호출 래퍼
todo_generator.py        # LLM → TodoItem 변환 (쿼터 초과 시 rule-based fallback)
database.py              # SQLite TodoStore (dict-like 인터페이스)
schemas.py               # Pydantic 스키마 정의
routes/
  todo.py                # /todos, /todos/generate 엔드포인트
  schedule.py            # /schedules 엔드포인트
```

### `/todos/generate` 동작 흐름

1. `DataCollector.collect_all()` → 학사 일정 크롤링
2. 크롤링 결과가 비면 `fake_schedules` (하드코딩 fallback) 사용
3. `TodoGenerator` → OpenAI 호출로 TODO 생성
4. OpenAI 429(쿼터 초과) 시 rule-based 방식으로 fallback → 최소 3개 생성
5. `_convert_generated_todo()` → `TodoResponse` 스키마로 변환 후 반환

---

## 환경 설정

```bash
pip install -r requirements.txt

# .env 파일 생성
echo "OPENAI_API_KEY=sk-xxxx" > .env

# 서버 실행
python -m uvicorn main:app --reload
```

---

## 주요 버그 수정 이력 (완료)

| 파일 | 문제 | 수정 내용 |
|------|------|-----------|
| `database.py` | Python 3.8에서 `list[dict]` 타입 힌트 오류 | `List[dict]` (typing 모듈) 로 변경 |
| `main.py` | Windows cp949 콘솔에서 이모지 UnicodeEncodeError | print 문의 이모지를 `[START]`/`[STOP]` 으로 교체 |
| `routes/todo.py` | `_convert_generated_todo`에서 `date` 객체를 문자열로 처리해 TypeError | `isinstance(item.due_date, date)` 분기 추가 |
| `routes/todo.py` | 크롤링 결과 없을 때 빈 TODO 반환 | `fake_schedules` 주입 로직 추가 |

---

## 현재 알려진 제약사항

- OpenAI API 쿼터 소진 상태일 수 있음 → rule-based fallback으로 최소 3개 생성
- 크롤러가 학교 서버 구조 변경 시 빈 결과 반환 가능 → `fake_schedules` 로 대응

---

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/todos/generate` | 크롤링 + LLM → TODO 자동 생성 |
| GET | `/todos` | 저장된 TODO 전체 조회 |
| POST | `/todos` | TODO 수동 추가 |
| PUT | `/todos/{id}` | TODO 수정 |
| DELETE | `/todos/{id}` | TODO 삭제 |
| GET | `/schedules` | 학사 일정 조회 |

---

## 코드 작성 규칙

- Python 3.8 호환성 유지: `list[str]` → `List[str]`, `dict[str, int]` → `Dict[str, int]`
- print 문에 이모지 사용 금지 (Windows cp949 콘솔 호환성)
- `.env` 파일은 절대 커밋 금지
- 작업 브랜치: `develop` (main은 최종 제출용)
