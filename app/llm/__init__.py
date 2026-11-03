import logging
from functools import lru_cache

from app.core.config import get_settings
from app.llm.base import ChatMessage, LLMError, LLMProvider
from app.llm.mock_provider import MockProvider

logger = logging.getLogger(__name__)


@lru_cache
def get_llm() -> LLMProvider:
    s = get_settings()
    match s.llm_provider:
        case "mock":
            return MockProvider()
        case "openai":
            if key := s.openai_api_key.get_secret_value():
                from app.llm.openai_provider import OpenAIProvider

                return OpenAIProvider(key, s.openai_model, s.openai_max_output_tokens, s.openai_timeout)
        case "anthropic":
            if key := s.anthropic_api_key.get_secret_value():
                from app.llm.anthropic_provider import AnthropicProvider

                return AnthropicProvider(key, s.anthropic_model, s.anthropic_max_tokens, s.anthropic_effort)
        case other:
            raise ValueError(f"알 수 없는 LLM_PROVIDER: {other}")

    logger.warning("LLM_PROVIDER=%s 의 API 키가 비어 있어 mock 모드로 동작합니다.", s.llm_provider)
    return MockProvider()


__all__ = ["ChatMessage", "LLMError", "LLMProvider", "get_llm"]
