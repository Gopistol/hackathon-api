import logging
from collections.abc import AsyncIterator

import openai
from openai import AsyncOpenAI

from app.llm.base import ChatMessage, LLMError

logger = logging.getLogger(__name__)


def _to_llm_error(e: openai.OpenAIError) -> LLMError:
    """SDK 예외를 사용자용 메시지로 바꾼다. 원본 메시지(키 일부가 들어갈 수 있음)는 응답·로그에 넣지 않는다."""
    status = getattr(e, "status_code", None)
    code = getattr(e, "code", None)
    logger.warning("OpenAI 호출 실패: %s status=%s code=%s", type(e).__name__, status, code)

    # APITimeoutError 는 APIConnectionError 의 하위 클래스라 먼저 검사한다.
    # 크레딧 소진은 재시도해도 안 풀린다. 스트림에서는 RateLimitError 가 아닌 APIError 로 올 수 있어 코드로 먼저 본다.
    if code in ("insufficient_quota", "credit_balance_exhausted"):
        return LLMError("OpenAI 크레딧이 부족합니다. 결제 설정을 확인하세요.", 503)
    if isinstance(e, openai.APITimeoutError):
        return LLMError("AI 응답 시간이 초과되었습니다. 잠시 후 다시 시도하세요.", 504)
    if isinstance(e, openai.APIConnectionError):
        return LLMError("AI 서버에 연결할 수 없습니다.", 502)
    if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return LLMError("서버의 OpenAI API 키 설정이 올바르지 않습니다.", 502)
    if isinstance(e, openai.RateLimitError):
        return LLMError("AI 호출 한도를 초과했습니다. 잠시 후 다시 시도하세요.", 429)
    if isinstance(e, (openai.BadRequestError, openai.NotFoundError)):
        return LLMError("AI 요청이 거부되었습니다. OPENAI_MODEL 이름과 입력 길이를 확인하세요.", 502)
    return LLMError("AI 호출 중 오류가 발생했습니다.", 502)


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        max_output_tokens: int,
        timeout: float,
        stt_model: str = "gpt-4o-mini-transcribe",
        max_retries: int = 0,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._stt_model = stt_model

    def _params(self, messages: list[ChatMessage], system: str, max_tokens: int | None = None) -> dict:
        return {
            "model": self._model,
            "instructions": system,
            "input": [{"role": m.role, "content": m.content} for m in messages],
            "max_output_tokens": max_tokens or self._max_output_tokens,
            # 대화 기록은 클라이언트가 매번 보내므로 OpenAI 쪽에 저장하지 않는다.
            "store": False,
        }

    async def complete(self, messages: list[ChatMessage], system: str, max_tokens: int | None = None) -> str:
        try:
            response = await self._client.responses.create(**self._params(messages, system, max_tokens))
        except openai.OpenAIError as e:
            raise _to_llm_error(e) from e
        if response.status == "incomplete" and response.incomplete_details:
            if response.incomplete_details.reason == "content_filter":
                raise LLMError("콘텐츠 정책에 걸렸습니다. 질문을 바꿔 보세요.", 422)
            # max_output_tokens 에 걸린 경우는 잘린 답이라도 돌려준다.
        return response.output_text

    async def stream(self, messages: list[ChatMessage], system: str) -> AsyncIterator[str]:
        try:
            stream = await self._client.responses.create(**self._params(messages, system), stream=True)
            async for event in stream:
                if event.type == "response.output_text.delta":
                    yield event.delta
                elif event.type in ("error", "response.failed"):
                    logger.warning("OpenAI 스트림 실패: %s", event.type)
                    raise LLMError("AI 응답 생성 중 오류가 발생했습니다.", 502)
        except openai.OpenAIError as e:
            raise _to_llm_error(e) from e

    async def transcribe(self, audio: bytes, filename: str) -> str:
        """한국어 음성 → 텍스트."""
        try:
            result = await self._client.audio.transcriptions.create(
                model=self._stt_model, file=(filename, audio), language="ko"
            )
        except openai.OpenAIError as e:
            raise _to_llm_error(e) from e
        return result.text
