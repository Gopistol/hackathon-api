"""사장님 등록 화면의 질문·확인 문장을 소리로 읽어 준다(어르신용).

응답에는 주소만 넣고, 음성은 프론트가 그 주소를 재생할 때 만들면서 바로 흘려보낸다(스트리밍).
그래서 기존 API 가 느려지지 않고 첫 소리도 빨리 나온다.
같은 문장은 한 번 만든 mp3 를 다시 쓴다. ElevenLabs → OpenAI 순서로 시도하고, 둘 다 안 되면
주소를 null 로 두어 지금처럼 글자로만 보인다.
"""

import hashlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.llm import LLMError, LLMProvider
from app.services.ai import is_mock

logger = logging.getLogger(__name__)

URL_PREFIX = "/api/v1/merchant/tts"


def _dir() -> Path:
    path = get_settings().media_path / "tts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key(text: str) -> str:
    s = get_settings()
    return hashlib.sha256(f"{s.elevenlabs_tts_voice_id}|{s.openai_tts_voice}|{text}".encode()).hexdigest()[:20]


def audio_url(llm: LLMProvider, text: str | None) -> str | None:
    """읽어 줄 문장을 등록하고 재생 주소를 돌려준다. 미리 등록한 문장만 음성으로 만들 수 있다."""
    if not text or is_mock(llm):
        return None
    if not get_settings().elevenlabs_api_key.get_secret_value() and not (hasattr(llm, "speak") or hasattr(llm, "speak_stream")):
        return None
    key = _key(text)
    note = _dir() / f"{key}.txt"
    if not note.exists():
        note.write_text(text, encoding="utf-8")
    return f"{URL_PREFIX}/{key}.mp3"


def cached(key: str) -> Path | None:
    mp3 = _dir() / f"{key}.mp3"
    return mp3 if mp3.exists() else None


def registered_text(key: str) -> str | None:
    note = _dir() / f"{key}.txt"
    return note.read_text(encoding="utf-8") if note.exists() else None


async def speech_chunks(llm: LLMProvider, key: str, text: str) -> AsyncIterator[bytes]:
    """음성을 만드는 대로 조각을 돌려준다 (첫 소리가 빨리 나오게). 끝까지 받으면 mp3 로 저장해 다음엔 파일로 보낸다.
    ElevenLabs → OpenAI 순서. 소리를 보내기 시작한 뒤의 실패는 다른 쪽으로 바꿀 수 없어 그대로 끝난다."""
    sources = [_elevenlabs_chunks(text)] if get_settings().elevenlabs_api_key.get_secret_value() else []
    sources.append(_openai_chunks(llm, text))
    for source in sources:
        data, started = bytearray(), False
        try:
            async for chunk in source:
                started = True
                data += chunk
                yield chunk
        except (httpx.HTTPError, LLMError, _TTSFailed) as e:
            if started:
                raise
            logger.warning("음성 합성 실패 %s, 다음 방법으로 대신합니다.", type(e).__name__)
            continue
        tmp = _dir() / f"{key}.part"
        tmp.write_bytes(bytes(data))
        tmp.replace(_dir() / f"{key}.mp3")
        return
    raise LLMError("음성을 만들 수 없어요.", 503)


class _TTSFailed(RuntimeError):
    pass


async def _elevenlabs_chunks(text: str) -> AsyncIterator[bytes]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=s.openai_timeout) as client:
        async with client.stream(
            "POST",
            f"https://api.elevenlabs.io/v1/text-to-speech/{s.elevenlabs_tts_voice_id}/stream",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": s.elevenlabs_api_key.get_secret_value()},
            json={
                "text": text,
                "model_id": s.elevenlabs_tts_model,
                # 어르신이 알아듣기 쉽게 조금 천천히, 들쭉날쭉하지 않게
                "voice_settings": {"stability": 0.6, "similarity_boost": 0.8, "speed": 0.92},
            },
        ) as r:
            if not r.is_success:
                logger.warning("ElevenLabs TTS 실패 status=%s", r.status_code)
                raise _TTSFailed
            async for chunk in r.aiter_bytes():
                yield chunk


async def _openai_chunks(llm: LLMProvider, text: str) -> AsyncIterator[bytes]:
    if stream := getattr(llm, "speak_stream", None):
        async for chunk in stream(text):
            yield chunk
    elif speak := getattr(llm, "speak", None):
        yield await speak(text)
    else:
        raise _TTSFailed
