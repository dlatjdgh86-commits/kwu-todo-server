"""
llm_client.py
LLM API 호출 추상화 모듈 — Claude(Anthropic) / OpenAI 지원

역할:
  - crawler.py가 수집한 CrawledData를 받아
    TODO 생성용 프롬프트를 만들고 LLM을 호출합니다.
  - todo_generator.py의 TodoGenerator에서 사용합니다.
"""

import os
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Union, Dict, List

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 응답 모델
# ──────────────────────────────────────────────

@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0

    def parse_json(self) -> Union[dict, list]:
        """응답에서 JSON 파싱 (마크다운 코드펜스 자동 제거)"""
        text = self.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
        return json.loads(text.strip())


# ──────────────────────────────────────────────
# 추상 클라이언트
# ──────────────────────────────────────────────

class BaseLLMClient(ABC):

    @abstractmethod
    def chat(
        self,
        user_message: str,
        system_prompt: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> LLMResponse: ...

    def chat_json(
        self,
        user_message: str,
        system_prompt: str = "",
        max_tokens: int = 2048,
    ) -> Union[dict, list]:
        """JSON 응답을 자동 파싱하여 반환"""
        system = (
            system_prompt
            + "\n\n반드시 JSON 형식으로만 응답하세요. "
            "마크다운 코드펜스나 추가 텍스트 없이 순수 JSON만 출력하세요."
        )
        resp = self.chat(user_message, system_prompt=system,
                         max_tokens=max_tokens, temperature=0.1)
        try:
            return resp.parse_json()
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 실패: {e}\n원본:\n{resp.content}")
            raise


# ──────────────────────────────────────────────
# Claude 클라이언트
# ──────────────────────────────────────────────

class ClaudeClient(BaseLLMClient):
    """
    Anthropic Claude API 클라이언트
    환경변수 ANTHROPIC_API_KEY 필요

    설정:
        Windows CMD: set ANTHROPIC_API_KEY=sk-ant-...
        Mac/Linux:   export ANTHROPIC_API_KEY=sk-ant-...
    """

    DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(self, api_key: str = None, model: str = None):
        try:
            import anthropic
            self._anthropic = anthropic
        except ImportError:
            raise ImportError("pip install anthropic")

        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY 환경변수가 필요합니다.")
        self.model  = model or self.DEFAULT_MODEL
        self.client = self._anthropic.Anthropic(api_key=self.api_key)
        logger.info(f"[Claude] 초기화 완료 (모델: {self.model})")

    def chat(self, user_message, system_prompt="", max_tokens=2048, temperature=0.3) -> LLMResponse:
        kwargs = {
            "model":       self.model,
            "max_tokens":  max_tokens,
            "temperature": temperature,
            "messages":    [{"role": "user", "content": user_message}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        try:
            msg = self.client.messages.create(**kwargs)
            return LLMResponse(
                content=msg.content[0].text if msg.content else "",
                model=self.model,
                input_tokens=msg.usage.input_tokens,
                output_tokens=msg.usage.output_tokens,
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
            raise ImportError("pip install openai")

        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY 환경변수가 필요합니다.")
        self.model  = model or self.DEFAULT_MODEL
        self.client = self._openai.OpenAI(api_key=self.api_key)
        logger.info(f"[OpenAI] 초기화 완료 (모델: {self.model})")

    def chat(self, user_message, system_prompt="", max_tokens=2048, temperature=0.3) -> LLMResponse:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        try:
            resp = self.client.chat.completions.create(
                model=self.model, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
            )
            choice = resp.choices[0]
            return LLMResponse(
                content=choice.message.content or "",
                model=self.model,
                input_tokens=resp.usage.prompt_tokens,
                output_tokens=resp.usage.completion_tokens,
            )
        except self._openai.OpenAIError as e:
            logger.error(f"[OpenAI] API 오류: {e}")
            raise


# ──────────────────────────────────────────────
# 프롬프트 템플릿
# ──────────────────────────────────────────────

class PromptTemplates:
    """todo_generator.py에서 사용하는 프롬프트 모음"""

    SYSTEM = """
당신은 광운대학교 학생의 학습을 돕는 AI TODO 어시스턴트입니다.
KLAS에서 수집한 과제, 퀴즈, 온라인강의, 학사일정 정보를 분석하여
실행 가능하고 우선순위가 명확한 TODO 목록을 JSON으로 생성합니다.

규칙:
1. 각 TODO는 동사로 시작하는 구체적인 행동 항목이어야 합니다.
2. 마감일이 있는 항목은 반드시 포함합니다.
3. 미제출 과제와 미수강 강의를 우선 처리합니다.
4. 우선순위: 1=긴급(오늘~2일) 2=높음(3~7일) 3=보통(1~2주) 4=여유(2주 이상)
5. subtasks는 실제로 해야 할 세부 행동을 씁니다.
""".strip()

    @staticmethod
    def build_prompt(data, today_str: str) -> str:
        """CrawledData → LLM 프롬프트 변환"""
        from crawler import CrawledData
        lines = [f"오늘 날짜: {today_str}\n"]

        # 학사일정
        lines.append("## 학사일정")
        if data.academic_events:
            for e in data.academic_events[:10]:
                end = f" ~ {e.end_date}" if e.end_date else ""
                lines.append(f"- [{e.category}] {e.title} ({e.start_date}{end})")
        else:
            lines.append("- 없음")

        # 과제/퀴즈
        lines.append("\n## KLAS 과제/퀴즈")
        if data.assignments:
            for a in data.assignments[:15]:
                due  = a.due_str or "마감일 미확인"
                stat = "✅ 제출완료" if a.is_submitted else "❌ 미제출"
                lines.append(f"- [{a.task_type}] {a.course_name}: {a.title} (마감: {due}) {stat}")
        else:
            lines.append("- 없음")

        # 온라인강의 미수강
        lines.append("\n## 미수강 온라인강의")
        if data.lectures:
            for lec in data.lectures[:10]:
                lines.append(f"- {lec.course_name} {lec.week}주차: {lec.title} (기한: {lec.deadline_str})")
        else:
            lines.append("- 없음")

        lines.append("""
위 정보를 분석하여 아래 JSON 형식으로 TODO를 생성하세요.
미제출 과제와 미수강 강의를 최우선으로 처리하세요.

{
  "todos": [
    {
      "id": "todo_001",
      "title": "TODO 제목 (동사로 시작)",
      "description": "상세 설명",
      "category": "과제 | 퀴즈 | 온라인강의 | 시험 | 학사일정 | 기타",
      "priority": 1,
      "priority_reason": "우선순위 결정 이유",
      "due_date": "YYYY-MM-DD 또는 null",
      "estimated_hours": 2.0,
      "subtasks": ["세부 작업 1", "세부 작업 2"],
      "source": "과목명 또는 출처"
    }
  ],
  "summary": "이번 주 핵심 할 일 한 줄 요약"
}
""")
        return "\n".join(lines)

    @staticmethod
    def build_priority_prompt(todos_json: str, today_str: str) -> str:
        return f"""
오늘 날짜: {today_str}

다음 TODO 목록의 priority와 priority_reason만 재검토하여
동일한 JSON 구조로 반환하세요.

{todos_json}
""".strip()


# ──────────────────────────────────────────────
# 팩토리
# ──────────────────────────────────────────────

def create_llm_client(provider: str = "claude", **kwargs) -> BaseLLMClient:
    """
    LLM 클라이언트 팩토리
    Args:
        provider: "claude" 또는 "openai"
    """
    p = provider.lower()
    if p in ("claude", "anthropic"):
        return ClaudeClient(**kwargs)
    elif p == "openai":
        return OpenAIClient(**kwargs)
    raise ValueError(f"지원하지 않는 provider: {provider}")


    # [추가] 수집된 항목을 정렬된 번호 배열로만 반환받는 프롬프트
    # todo_generator.py의 sort_by_llm()에서 사용
    # 출력 형식: [과제] 컴퓨터구조: Project (마감: 04/22) 정렬용
    @staticmethod
    def build_sort_prompt(task_list_str: str, today_str: str) -> str:
        return f"""오늘 날짜: {today_str}

아래 할 일 목록을 긴급도 순서로 정렬해줘.
기준:
1. 미제출/미수강 항목을 제출완료보다 앞에 배치
2. 마감이 임박한 순서대로 정렬
3. 마감일 없는 항목은 마지막에 배치

{task_list_str}

정렬된 번호 순서만 JSON 배열로 반환해. 예시: [3, 1, 4, 2, 5]
다른 텍스트 없이 JSON 배열만 반환해."""
