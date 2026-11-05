"""ElevenLabs: 사장님 녹음으로 목소리를 복제해 외국어로 더빙하고, 자막 시간을 음성에 맞춘다.

복제한 목소리는 계정 슬롯(Starter 10개)을 차지하므로 영상을 다 만들면 지운다.
원본 에러 본문에는 계정 정보가 섞일 수 있어 응답·로그에는 상태 코드만 남긴다.
"""

import asyncio
import logging
import uuid

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

API = "https://api.elevenlabs.io/v1"
TIMEOUT = 30.0


# Starter 요금제는 동시 요청이 3개까지다. 넘으면 429 가 나서 다른 목소리(OpenAI)로 바뀌므로 줄을 세운다.
slots = asyncio.Semaphore(3)


class ElevenLabsError(RuntimeError):
    pass


def enabled() -> bool:
    s = get_settings()
    return s.llm_provider != "mock" and bool(s.elevenlabs_api_key.get_secret_value())


def _client() -> httpx.AsyncClient:
    key = get_settings().elevenlabs_api_key.get_secret_value()
    return httpx.AsyncClient(base_url=API, headers={"xi-api-key": key}, timeout=TIMEOUT)


async def _post(action: str, path: str, **kwargs) -> httpx.Response:
    try:
        async with slots, _client() as client:
            r = await client.post(path, **kwargs)
    except httpx.HTTPError as e:
        logger.warning("ElevenLabs %s 연결 실패: %s", action, type(e).__name__)
        raise ElevenLabsError(action) from e
    if not r.is_success:
        logger.warning("ElevenLabs %s 실패 status=%s", action, r.status_code)
        raise ElevenLabsError(action)
    return r


async def clone_voice(audio: bytes, filename: str) -> str:
    """녹음으로 목소리를 복제하고 voice_id 를 돌려준다. 시장 소음은 ElevenLabs 가 걸러 낸다."""
    r = await _post(
        "목소리 복제",
        "/voices/add",
        data={"name": f"shortform-{uuid.uuid4().hex[:8]}", "remove_background_noise": "true"},
        files={"files": (filename, audio)},
    )
    return r.json()["voice_id"]


async def speak(text: str, voice_id: str) -> bytes:
    r = await _post(
        "더빙",
        f"/text-to-speech/{voice_id}",
        params={"output_format": "mp3_44100_128"},
        json={
            "text": text,
            "model_id": get_settings().elevenlabs_dub_model,
            # 사장님 목소리에 가깝게(similarity), 과장되지 않게(style 낮게)
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.85, "style": 0.2, "use_speaker_boost": True},
        },
    )
    return r.content


async def delete_voice(voice_id: str) -> None:
    """실패해도 영상에는 영향이 없어 로그만 남긴다."""
    try:
        async with _client() as client:
            r = await client.delete(f"/voices/{voice_id}")
        if not r.is_success:
            logger.warning("복제 목소리 삭제 실패 status=%s", r.status_code)
    except httpx.HTTPError as e:
        logger.warning("복제 목소리 삭제 연결 실패: %s", type(e).__name__)


async def align(audio: bytes, filename: str, text: str) -> list[tuple[str, float, float]]:
    """음성과 대본을 맞춰 글자마다 (글자, 시작초, 끝초)를 돌려준다. 자막 시간에 쓴다."""
    r = await _post("자막 시간 맞추기", "/forced-alignment", files={"file": (filename, audio)}, data={"text": text})
    return [(c["text"], c["start"], c["end"]) for c in r.json()["characters"]]
