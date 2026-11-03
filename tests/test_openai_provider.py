"""OpenAIProvider 를 실제 네트워크 없이 검증한다. SDK 클라이언트 호출만 가짜로 바꾼다."""

from types import SimpleNamespace

import httpx
import openai
import pytest

from app.core.config import get_settings
from app.llm import get_llm
from app.llm.mock_provider import MockProvider
from app.llm.openai_provider import OpenAIProvider
from app.main import app

FAKE_KEY = "sk-test-DO-NOT-LEAK-1234"
_REQ = httpx.Request("POST", "https://api.openai.com/v1/responses")


def _status_error(cls, status: int, code: str | None = None):
    body = {"error": {"message": f"Incorrect API key provided: {FAKE_KEY}", "code": code}}
    return cls("error", response=httpx.Response(status, request=_REQ, json=body), body=body["error"])


@pytest.fixture
def provider(client):
    p = OpenAIProvider(FAKE_KEY, "gpt-4.1-mini", 100, 5.0)
    app.dependency_overrides[get_llm] = lambda: p
    yield p
    app.dependency_overrides.clear()


def _fake_create(result=None, error=None):
    async def create(**kwargs):
        create.kwargs = kwargs
        if error is not None:
            raise error
        return result

    return create


def test_ask_success(client, provider, monkeypatch) -> None:
    fake = _fake_create(SimpleNamespace(status="completed", incomplete_details=None, output_text="안녕하세요"))
    monkeypatch.setattr(provider._client.responses, "create", fake)

    response = client.post("/ask", json={"question": "인사해 줘"})

    assert response.status_code == 200
    assert response.json() == {"answer": "안녕하세요", "mode": "openai"}
    assert fake.kwargs["model"] == "gpt-4.1-mini"
    assert fake.kwargs["input"] == [{"role": "user", "content": "인사해 줘"}]
    assert fake.kwargs["instructions"]  # DEFAULT_SYSTEM_PROMPT


def test_chat_stream_success(client, provider, monkeypatch) -> None:
    async def events():
        yield SimpleNamespace(type="response.created")
        yield SimpleNamespace(type="response.output_text.delta", delta="안녕")
        yield SimpleNamespace(type="response.output_text.delta", delta="하세요")
        yield SimpleNamespace(type="response.completed")

    monkeypatch.setattr(provider._client.responses, "create", _fake_create(events()))

    text = client.post("/api/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]}).text

    assert 'data: {"text": "안녕"}' in text
    assert 'data: {"text": "하세요"}' in text
    assert text.rstrip().endswith('data: {"mode": "openai"}')


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (openai.APITimeoutError(request=_REQ), 504),
        (openai.APIConnectionError(request=_REQ), 502),
        (_status_error(openai.AuthenticationError, 401, "invalid_api_key"), 502),
        (_status_error(openai.RateLimitError, 429, "rate_limit_exceeded"), 429),
        (_status_error(openai.RateLimitError, 429, "insufficient_quota"), 503),
        (_status_error(openai.NotFoundError, 404, "model_not_found"), 502),
    ],
)
def test_errors_map_to_status_without_leaking_key(client, provider, monkeypatch, caplog, error, status) -> None:
    monkeypatch.setattr(provider._client.responses, "create", _fake_create(error=error))

    response = client.post("/ask", json={"question": "hello"})

    assert response.status_code == status
    assert FAKE_KEY not in response.text
    assert FAKE_KEY not in caplog.text


def test_stream_error_becomes_sse_error_event(client, provider, monkeypatch) -> None:
    error = _status_error(openai.RateLimitError, 429, "rate_limit_exceeded")
    monkeypatch.setattr(provider._client.responses, "create", _fake_create(error=error))

    text = client.post("/api/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]}).text

    assert "event: error" in text
    assert "한도" in text
    assert FAKE_KEY not in text


def test_openai_without_key_falls_back_to_mock(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    get_llm.cache_clear()
    try:
        assert isinstance(get_llm(), MockProvider)
    finally:
        get_settings.cache_clear()
        get_llm.cache_clear()


def test_settings_repr_hides_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    from app.core.config import Settings

    assert FAKE_KEY not in repr(Settings())
