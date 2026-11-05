import asyncio
from collections.abc import AsyncIterator

from app.llm.base import ChatMessage


class MockProvider:
    """API 키 없이 UI·프론트 연동할 때 쓰는 가짜 프로바이더. 토큰을 쓰지 않는다."""

    name = "mock"

    def _answer(self, messages: list[ChatMessage]) -> str:
        question = " ".join(messages[-1].content.split())[:160]
        return f"[mock mode] LLM 키가 설정되지 않았습니다.\n\n질문: {question}"

    async def complete(self, messages: list[ChatMessage], system: str, max_tokens: int | None = None) -> str:
        return self._answer(messages)

    async def stream(self, messages: list[ChatMessage], system: str) -> AsyncIterator[str]:
        for word in self._answer(messages).split(" "):
            await asyncio.sleep(0.03)
            yield word + " "
