from app.llm import get_llm
from app.main import app

BASE = "/api/v1/merchant/onboarding/sessions"


class SpeakingLLM:
    """음성 합성이 되는 가짜 프로바이더. 정리·번역은 쓰지 않는 단계(store_name)만 테스트한다."""

    name = "openai"
    calls = 0

    async def speak(self, text: str) -> bytes:
        SpeakingLLM.calls += 1
        return b"ID3fake-mp3"


def test_mock_mode_has_no_audio(client) -> None:
    start = client.post(BASE, json={}).json()
    assert start["audio_url"] is None
    answer = client.post(f"{BASE}/{start['session_id']}/answers", data={"text": "포방터 순대집이에요"}).json()
    assert answer["confirm_audio_url"] is None


def test_question_and_confirm_audio(client) -> None:
    app.dependency_overrides[get_llm] = SpeakingLLM
    try:
        start = client.post(BASE, json={}).json()
        url = start["audio_url"]
        assert url.startswith("/api/v1/merchant/tts/") and url.endswith(".mp3")

        audio = client.get(url)
        assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg"
        assert audio.content == b"ID3fake-mp3"
        client.get(url)
        assert SpeakingLLM.calls == 1  # 같은 문장은 다시 만들지 않는다

        sid = start["session_id"]
        answer = client.post(f"{BASE}/{sid}/answers", data={"text": "포방터 순대집이에요"}).json()
        assert answer["confirm_audio_url"] and answer["confirm_audio_url"] != url

        nxt = client.post(f"{BASE}/{sid}/confirm", json={"step": "store_name", "accepted": True}).json()
        assert nxt["step"] == "menus" and nxt["audio_url"]
    finally:
        app.dependency_overrides.pop(get_llm, None)


def test_unknown_audio_key(client) -> None:
    assert client.get("/api/v1/merchant/tts/0123456789abcdef0123.mp3").status_code == 404
    assert client.get("/api/v1/merchant/tts/not-a-key.mp3").status_code == 422


class StreamingLLM:
    """만드는 대로 조각을 보내는 프로바이더."""

    name = "openai"
    calls = 0

    async def speak_stream(self, text: str):
        StreamingLLM.calls += 1
        for chunk in (b"ID3", b"part-1", b"part-2"):
            yield chunk


def test_tts_streams_first_then_serves_saved_file(client) -> None:
    app.dependency_overrides[get_llm] = StreamingLLM
    try:
        url = client.post("/api/v1/merchant/interviews", json={"kind": "register"}).json()["audio_url"]
        first = client.get(url)
        assert first.status_code == 200 and first.content == b"ID3part-1part-2"
        again = client.get(url)  # 두 번째는 저장한 파일
        assert again.content == first.content and "content-length" in again.headers
        assert StreamingLLM.calls == 1
    finally:
        app.dependency_overrides.pop(get_llm, None)
