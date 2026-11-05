from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


class LLMError(RuntimeError):
    """프로바이더 호출 실패나 모델 거절. 라우터에서 status_code 로 HTTP 에러를 낸다.

    메시지는 사용자에게 그대로 보이므로 키나 원본 에러 내용을 넣지 않는다.
    """

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMProvider(Protocol):
    """모든 LLM 프로바이더가 따르는 인터페이스.

    새 프로바이더는 name 과 두 메서드를 구현한 클래스를 만들고
    app/llm/__init__.py 의 get_llm() 에 등록하면 된다.
    """

    # 응답의 mode 로 내려간다 (UI 에서 mock/openai 구분용)
    name: str

    async def complete(self, messages: list[ChatMessage], system: str) -> str: ...

    def stream(self, messages: list[ChatMessage], system: str) -> AsyncIterator[str]: ...

    # 음성 인식은 선택. 없는 프로바이더(anthropic, mock)는 app/services/ai.py 에서 처리한다.
    # async def transcribe(self, audio: bytes, filename: str) -> str: ...
