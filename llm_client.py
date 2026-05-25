"""
llm_client.py
LLM API 호출 추상화 모듈 - Claude(Anthropic) 및 OpenAI 지원
"""

import os
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 응답 데이터 모델
# ──────────────────────────────────────────────

@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    raw: dict = None  # 원본 응답 보존

    def parse_json(self) -> dict | list:
        """응답에서 JSON 파싱 (```json 펜스 자동 제거)"""
        text = self.content.strip()
        # 마크다운 코드펜스 제거
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
        return json.loads(text.strip())


# ──────────────────────────────────────────────
# 추상 클라이언트
# ──────────────────────────────────────────────

class BaseLLMClient(ABC):
    """모든 LLM 클라이언트의 공통 인터페이스"""

    @abstractmethod
    def chat(
        self,
        user_message: str,
        system_prompt: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> LLMResponse:
        ...

    def chat_json(
        self,
        user_message: str,
        system_prompt: str = "",
        max_tokens: int = 2048,
    ) -> dict | list:
        """JSON 응답을 자동 파싱하여 반환"""
        json_system = (
            system_prompt + "\n\n반드시 JSON 형식으로만 응답하세요. "
            "마크다운 코드펜스나 추가 텍스트 없이 순수 JSON만 출력하세요."
        )
        response = self.chat(user_message, system_prompt=json_system, max_tokens=max_tokens, temperature=0.1)
        try:
            return response.parse_json()
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 실패: {e}\n원본 응답:\n{response.content}")
            raise


# ──────────────────────────────────────────────
# Claude (Anthropic) 클라이언트
# ──────────────────────────────────────────────

class ClaudeClient(BaseLLMClient):
    """
    Anthropic Claude API 클라이언트
    환경변수 ANTHROPIC_API_KEY 필요
    """

    DEFAULT_MODEL = "claude-sonnet-4-20250514"

    def __init__(self, api_key: str = None, model: str = None):
        try:
            import anthropic
            self._anthropic = anthropic
        except ImportError:
            raise ImportError("anthropic 패키지를 설치하세요: pip install anthropic")

        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY 환경변수 또는 api_key 인자가 필요합니다")
        self.model = model or self.DEFAULT_MODEL
        self.client = self._anthropic.Anthropic(api_key=self.api_key)
        logger.info(f"[Claude] 클라이언트 초기화 완료 (모델: {self.model})")

    def chat(
        self,
        user_message: str,
        system_prompt: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> LLMResponse:
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": user_message}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        try:
            msg = self.client.messages.create(**kwargs)
            content = msg.content[0].text if msg.content else ""
            return LLMResponse(
                content=content,
                model=self.model,
                input_tokens=msg.usage.input_tokens,
                output_tokens=msg.usage.output_tokens,
                raw=msg.model_dump() if hasattr(msg, "model_dump") else {},
            )
        except self._anthropic.APIError as e:
            logger.error(f"[Claude] API 오류: {e}")
            raise


# ──────────────────────────────────────────────
# OpenAI 클라이언트
# ──────────────────────────────────────────────

class OpenAIClient(BaseLLMClient):
    """
    OpenAI GPT API 클라이언트
    환경변수 OPENAI_API_KEY 필요
    """

    DEFAULT_MODEL = "gpt-4o"

    def __init__(self, api_key: str = None, model: str = None):
        try:
            import openai
            self._openai = openai
        except ImportError:
            raise ImportError("openai 패키지를 설치하세요: pip install openai")

        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY 환경변수 또는 api_key 인자가 필요합니다")
        self.model = model or self.DEFAULT_MODEL
        self.client = self._openai.OpenAI(api_key=self.api_key)
        logger.info(f"[OpenAI] 클라이언트 초기화 완료 (모델: {self.model})")

    def chat(
        self,
        user_message: str,
        system_prompt: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> LLMResponse:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            choice = resp.choices[0]
            return LLMResponse(
                content=choice.message.content or "",
                model=self.model,
                input_tokens=resp.usage.prompt_tokens,
                output_tokens=resp.usage.completion_tokens,
                raw=resp.model_dump() if hasattr(resp, "model_dump") else {},
            )
        except self._openai.OpenAIError as e:
            logger.error(f"[OpenAI] API 오류: {e}")
            raise


# ──────────────────────────────────────────────
# 프롬프트 템플릿
# ──────────────────────────────────────────────

class PromptTemplates:
    """TODO 생성에 사용할 프롬프트 모음"""

    SYSTEM_TODO_GENERATOR = """
당신은 대학생의 학습 관리를 돕는 스마트 TODO 생성 어시스턴트입니다.
주어진 학사 일정, LMS 과제, 에브리타임 정보를 분석하여
실행 가능하고 우선순위가 명확한 TODO 항목을 생성합니다.

규칙:
1. 각 TODO는 구체적이고 실행 가능해야 합니다 (동사로 시작)
2. 마감일이 있는 항목은 반드시 포함합니다
3. 예상 소요 시간을 현실적으로 추정합니다
4. 관련 있는 항목끼리 묶어서 서브태스크를 생성합니다
5. 에브리타임 정보에서 시험 범위나 팁을 TODO에 녹여냅니다
""".strip()

    @staticmethod
    def build_todo_prompt(
        academic_events: list,
        lms_assignments: list,
        everytime_posts: list,
        today_str: str,
    ) -> str:
        """TODO 생성용 프롬프트 빌드"""
        lines = [f"오늘 날짜: {today_str}\n"]

        # 학사 일정
        lines.append("## 학사 일정")
        if academic_events:
            for e in academic_events[:10]:
                end_info = f" ~ {e.end_date}" if e.end_date else ""
                lines.append(f"- [{e.category}] {e.title} ({e.start_date}{end_info})")
        else:
            lines.append("- 없음")

        # LMS 과제
        lines.append("\n## LMS 과제/퀴즈")
        if lms_assignments:
            for a in lms_assignments[:10]:
                due = a.due_date.strftime("%Y-%m-%d %H:%M") if a.due_date else "마감일 미확인"
                lines.append(f"- [{a.assignment_type}] {a.course_name}: {a.title} (마감: {due})")
        else:
            lines.append("- 없음")

        # 에브리타임
        lines.append("\n## 에브리타임 주요 게시글")
        if everytime_posts:
            for p in everytime_posts[:5]:
                kw = ", ".join(p.keywords) if p.keywords else ""
                lines.append(f"- [{p.board}] {p.title} (키워드: {kw})")
                if p.body:
                    lines.append(f"  내용: {p.body[:150]}...")
        else:
            lines.append("- 없음")

        lines.append("""
위 정보를 바탕으로 TODO 목록을 아래 JSON 형식으로 생성하세요.
우선순위는 1(긴급) ~ 4(여유) 척도를 사용합니다.

{
  "todos": [
    {
      "id": "todo_001",
      "title": "TODO 제목",
      "description": "상세 설명",
      "category": "학사일정 | LMS과제 | 시험준비 | 에브리타임",
      "priority": 1,
      "priority_reason": "우선순위 결정 이유",
      "due_date": "YYYY-MM-DD 또는 null",
      "estimated_hours": 2.5,
      "subtasks": ["세부 작업 1", "세부 작업 2"],
      "source": "데이터 출처"
    }
  ],
  "summary": "이번 주 핵심 할 일 한 줄 요약"
}
""")
        return "\n".join(lines)

    @staticmethod
    def build_priority_prompt(todos_json: str, today_str: str) -> str:
        """생성된 TODO의 우선순위 재검토 프롬프트"""
        return f"""
오늘 날짜: {today_str}

다음 TODO 목록의 우선순위를 마감일, 중요도, 소요 시간을 종합하여 재검토하세요.
각 항목의 priority 점수(1~4)와 priority_reason을 업데이트한 JSON을 반환하세요.

{todos_json}

반환 형식: 동일한 JSON 구조로 priority와 priority_reason만 수정하여 반환.
""".strip()


# ──────────────────────────────────────────────
# 팩토리 함수
# ──────────────────────────────────────────────

def create_llm_client(provider: str = "claude", **kwargs) -> BaseLLMClient:
    """
    LLM 클라이언트 팩토리

    Args:
        provider: "claude" 또는 "openai"
        **kwargs: api_key, model 등 클라이언트별 옵션

    Returns:
        BaseLLMClient 인스턴스
    """
    provider = provider.lower()
    if provider in ("claude", "anthropic"):
        return ClaudeClient(**kwargs)
    elif provider == "openai":
        return OpenAIClient(**kwargs)
    else:
        raise ValueError(f"지원하지 않는 provider: {provider}. 'claude' 또는 'openai'를 사용하세요.")


# ──────────────────────────────────────────────
# 실행 예시
# ──────────────────────────────────────────────

if __name__ == "__main__":
    # Claude 테스트
    client = create_llm_client("claude")
    response = client.chat(
        user_message="안녕하세요! 간단한 연결 테스트입니다.",
        system_prompt="당신은 친절한 학습 도우미입니다.",
    )
    print(f"응답: {response.content}")
    print(f"토큰 사용: 입력 {response.input_tokens} / 출력 {response.output_tokens}")
