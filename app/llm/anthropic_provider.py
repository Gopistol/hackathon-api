from collections.abc import AsyncIterator

import anthropic

from app.llm.base import ChatMessage, LLMError

# 모델이 정책상 거절하면 서버 쪽에서 다른 모델로 자동 재시도한다.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, max_tokens: int, effort: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._effort = effort

    def _params(self, messages: list[ChatMessage], system: str) -> dict:
        params: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "output_config": {"effort": self._effort},
            "betas": [_FALLBACK_BETA],
            "fallbacks": "default",
            "system": system,
        }
        return params

    async def complete(self, messages: list[ChatMessage], system: str, max_tokens: int | None = None) -> str:
        # effort(사고)를 쓰므로 짧은 상한은 적용하지 않는다.
        try:
            async with self._client.beta.messages.stream(**self._params(messages, system)) as stream:
                message = await stream.get_final_message()
        except anthropic.APIError as e:
            raise LLMError("AI 호출 중 오류가 발생했습니다.") from e
        if message.stop_reason == "refusal":
            raise LLMError("모델이 요청을 거절했습니다.")
        return "".join(b.text for b in message.content if b.type == "text")

    async def stream(self, messages: list[ChatMessage], system: str) -> AsyncIterator[str]:
        try:
            async with self._client.beta.messages.stream(**self._params(messages, system)) as stream:
                async for text in stream.text_stream:
                    yield text
                message = await stream.get_final_message()
        except anthropic.APIError as e:
            raise LLMError("AI 호출 중 오류가 발생했습니다.") from e
        if message.stop_reason == "refusal":
            raise LLMError("모델이 요청을 거절했습니다.")
