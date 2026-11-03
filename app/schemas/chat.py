from typing import Literal

from pydantic import BaseModel, Field


class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    # 대화 기록 전체를 보낸다. 마지막은 user 메시지여야 한다.
    messages: list[ChatMessageIn] = Field(min_length=1, max_length=50)
    # 비우면 app/prompts.py 의 DEFAULT_SYSTEM_PROMPT 를 쓴다.
    system: str | None = Field(default=None, max_length=4000)


class ChatResponse(BaseModel):
    content: str
    mode: str  # mock | openai | anthropic


class AskRequest(BaseModel):
    """단발 질문용. 공식 샘플의 /ask 와 같은 형태."""

    question: str = Field(min_length=2, max_length=2000)
    system_prompt: str | None = Field(default=None, max_length=4000)


class AskResponse(BaseModel):
    answer: str
    mode: str
