"""실제 OpenAI API 호출 테스트 (비용 발생). 기본 pytest 실행에서는 제외된다.

실행: OPENAI_API_KEY 를 .env 또는 환경변수에 넣고 `pytest -m live`
"""

import asyncio

import pytest

from app.core.config import Settings
from app.llm.base import ChatMessage
from app.llm.openai_provider import OpenAIProvider

pytestmark = pytest.mark.live

settings = Settings()
key = settings.openai_api_key.get_secret_value()


@pytest.mark.skipif(not key, reason="OPENAI_API_KEY 가 없습니다")
def test_live_complete_and_stream() -> None:
    provider = OpenAIProvider(key, settings.openai_model, 50, settings.openai_timeout)
    messages = [ChatMessage(role="user", content="Reply with the single word: pong")]

    async def run() -> tuple[str, str]:
        answer = await provider.complete(messages, "You are a test bot.")
        streamed = "".join([t async for t in provider.stream(messages, "You are a test bot.")])
        return answer, streamed

    answer, streamed = asyncio.run(run())
    assert "pong" in answer.lower()
    assert "pong" in streamed.lower()
