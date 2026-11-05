"""OpenAIProvider 와 AI 대체 동작을 실제 네트워크 없이 검증한다. SDK 호출만 가짜로 바꾼다."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.core.config import get_settings
from app.llm import LLMError, get_llm
from app.llm.mock_provider import MockProvider
from app.llm.openai_provider import OpenAIProvider
from app.main import app
from app.services import ai
from tests.utils import assert_error

FAKE_KEY = "sk-test-DO-NOT-LEAK-1234"
_REQ = httpx.Request("POST", "https://api.openai.com/v1/responses")


def _status_error(cls, status: int, code: str | None = None):
    body = {"error": {"message": f"Incorrect API key provided: {FAKE_KEY}", "code": code}}
    return cls("error", response=httpx.Response(status, request=_REQ, json=body), body=body["error"])


def _fake(result=None, error=None):
    async def create(**kwargs):
        create.calls = getattr(create, "calls", 0) + 1
        create.kwargs = kwargs
        if error is not None:
            raise error
        return result(create.calls) if callable(result) else result

    return create


def _text(output: str):
    return SimpleNamespace(status="completed", incomplete_details=None, output_text=output)


@pytest.fixture
def provider(client):
    p = OpenAIProvider(FAKE_KEY, "gpt-4.1-mini", 100, 5.0)
    app.dependency_overrides[get_llm] = lambda: p
    yield p
    app.dependency_overrides.clear()


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
def test_errors_map_to_status_without_leaking_key(provider, monkeypatch, caplog, error, status) -> None:
    monkeypatch.setattr(provider._client.responses, "create", _fake(error=error))
    with pytest.raises(LLMError) as e:
        asyncio.run(provider.complete([], "sys"))
    assert e.value.status_code == status
    assert FAKE_KEY not in str(e.value)
    assert FAKE_KEY not in caplog.text


def test_structure_uses_llm_json(provider, monkeypatch) -> None:
    out = json.dumps({"menus": [{"name_ko": "떡볶이", "price": 4000, "spicy_level": 2}], "needs_check": []})
    monkeypatch.setattr(provider._client.responses, "create", _fake(_text(f"```json\n{out}\n```")))
    parsed, needs = asyncio.run(ai.structure_step(provider, "menus", "떡볶이 사천원"))
    assert parsed["menus"][0]["price"] == 4000 and needs == []


def test_structure_retries_once_then_falls_back_to_rules(provider, monkeypatch) -> None:
    fake = _fake(_text("이건 JSON 이 아니에요"))
    monkeypatch.setattr(provider._client.responses, "create", fake)
    parsed, needs = asyncio.run(ai.structure_step(provider, "menus", "순대 5000원"))
    assert fake.calls == 2
    assert parsed["menus"][0] == {"name_ko": "순대", "price": 5000, "spicy_level": 0, "is_ai_guess": True}
    assert "확인" in needs[0]


def test_translation_failure_falls_back_to_korean(provider, monkeypatch) -> None:
    error = _status_error(openai.RateLimitError, 429, "insufficient_quota")
    monkeypatch.setattr(provider._client.responses, "create", _fake(error=error))
    result = asyncio.run(ai.translate_store(provider, "테스트 가게", "소개", [{"id": "m0", "name_ko": "새메뉴", "price": 1}]))
    assert result["en"]["store"]["name"] == "테스트 가게"
    assert result["en"]["menus"]["m0"]["name"] == "새메뉴"


def test_stt_failure_returns_stt_failed(client, provider, monkeypatch) -> None:
    error = _status_error(openai.BadRequestError, 400, "invalid_audio")
    monkeypatch.setattr(provider._client.audio.transcriptions, "create", _fake(error=error))
    sid = client.post("/api/v1/merchant/onboarding/sessions").json()["session_id"]
    r = client.post(
        f"/api/v1/merchant/onboarding/sessions/{sid}/answers", files={"audio": ("a.webm", b"x", "audio/webm")}
    )
    assert_error(r, 502, "STT_FAILED")
    assert FAKE_KEY not in r.text


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


def test_llm_wrong_price_is_corrected_by_rules(provider, monkeypatch) -> None:
    out = json.dumps({"menus": [{"name_ko": "기름떡볶이", "price": 3000, "spicy_level": 3, "is_ai_guess": True}]})
    monkeypatch.setattr(provider._client.responses, "create", _fake(_text(out)))
    parsed, needs = asyncio.run(ai.structure_step(provider, "menus", "기름떡볶이 사천 원이요"))
    assert parsed["menus"][0]["price"] == 4000
    assert parsed["menus"][0]["spicy_level"] == 1  # 용어집 값
    assert any("4,000원" in n for n in needs)


def test_blob_filename_gets_extension_from_content(client, provider, monkeypatch) -> None:
    fake = _fake(SimpleNamespace(text="영천 할매 떡볶이예요"))
    monkeypatch.setattr(provider._client.audio.transcriptions, "create", fake)
    monkeypatch.setattr(provider._client.responses, "create", _fake(_text('{"name_ko": "영천 할매 떡볶이"}')))
    sid = client.post("/api/v1/merchant/onboarding/sessions").json()["session_id"]
    webm = b"\x1aE\xdf\xa3" + b"\x00" * 20
    r = client.post(f"/api/v1/merchant/onboarding/sessions/{sid}/answers", files={"audio": ("blob", webm, "audio/webm")})
    assert r.status_code == 200, r.text
    assert fake.kwargs["file"][0] == "audio.webm"
    assert r.json()["parsed"]["name_ko"] == "영천 할매 떡볶이"


@pytest.mark.parametrize(
    ("head", "ext"),
    [(b"\x1aE\xdf\xa3", "webm"), (b"\x00\x00\x00\x20ftypM4A ", "m4a"), (b"OggS", "ogg"), (b"RIFF", "wav"), (b"ID3\x04", "mp3")],
)
def test_audio_ext(head, ext) -> None:
    assert ai.audio_ext(head + b"\x00" * 16) == ext
