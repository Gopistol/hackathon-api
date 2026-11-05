"""사장님 등록 화면의 질문·확인 문장을 소리로 읽어 준다(어르신용).

응답에는 주소만 넣고, 음성은 프론트가 그 주소를 재생할 때 만든다. 그래서 기존 API 가 느려지지 않는다.
같은 문장은 한 번 만든 mp3 를 다시 쓴다. ElevenLabs → OpenAI 순서로 시도하고, 둘 다 안 되면
주소를 null 로 두어 지금처럼 글자로만 보인다.
"""

import hashlib
import logging
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
    if not get_settings().elevenlabs_api_key.get_secret_value() and not hasattr(llm, "speak"):
        return None
    key = _key(text)
    note = _dir() / f"{key}.txt"
    if not note.exists():
        note.write_text(text, encoding="utf-8")
    return f"{URL_PREFIX}/{key}.mp3"


async def audio_file(llm: LLMProvider, key: str) -> Path | None:
    """주소의 mp3. 아직 없으면 만든다. 등록되지 않은 문장이면 None."""
    mp3, note = _dir() / f"{key}.mp3", _dir() / f"{key}.txt"
    if mp3.exists():
        return mp3
    if not note.exists():
        return None
    data = await _speak(llm, note.read_text(encoding="utf-8"))
    mp3.write_bytes(data)
    return mp3


async def _speak(llm: LLMProvider, text: str) -> bytes:
    s = get_settings()
    if key := s.elevenlabs_api_key.get_secret_value():
        try:
            async with httpx.AsyncClient(timeout=s.openai_timeout) as client:
                r = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{s.elevenlabs_tts_voice_id}",
                    params={"output_format": "mp3_44100_128"},
                    headers={"xi-api-key": key},
                    json={
                        "text": text,
                        "model_id": s.elevenlabs_tts_model,
                        # 어르신이 알아듣기 쉽게 조금 천천히, 들쭉날쭉하지 않게
                        "voice_settings": {"stability": 0.6, "similarity_boost": 0.8, "speed": 0.92},
                    },
                )
            if r.is_success:
                return r.content
            logger.warning("ElevenLabs TTS 실패 status=%s, OpenAI 로 대신합니다.", r.status_code)
        except httpx.HTTPError as e:
            logger.warning("ElevenLabs TTS 연결 실패 %s, OpenAI 로 대신합니다.", type(e).__name__)
    speak = getattr(llm, "speak", None)
    if speak is None:
        raise LLMError("음성을 만들 수 없어요.", 503)
    return await speak(text)
