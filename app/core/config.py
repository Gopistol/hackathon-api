from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수 설정. 로컬은 .env, 서버는 /etc/hackathon/hackathon.env (systemd EnvironmentFile)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Market Shorts API"
    root_path: str = ""
    # 콤마로 구분한 프론트 주소
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    # 주소가 바뀌는 무료 호스팅(Vercel 미리보기 등)용 정규식. 예: ^https://[a-z0-9-]+\.vercel\.app$
    cors_origin_regex: str = ""

    database_url: str = "sqlite:///./data/app.db"
    # /media 로 공개되는 파일(영상·썸네일·자막·사진)
    media_dir: str = "./data/media"
    # 사장님 원문 음성. 공개하지 않는다.
    audio_dir: str = "./data/audio"

    # LLM: openai | anthropic | mock. 선택한 프로바이더의 키가 비어 있으면 mock 으로 동작한다.
    # mock 이면 STT·정리·번역·자막도 전부 고정 응답으로 바뀐다.
    llm_provider: str = "openai"

    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4.1-mini"
    openai_stt_model: str = "gpt-4o-mini-transcribe"
    # 등록 질문·확인 문장을 소리로 읽어 주는 음성 합성(TTS)
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "coral"
    # ElevenLabs 가 있으면 등록 음성은 이쪽을 먼저 쓴다 (한국어가 더 자연스럽다). 없거나 실패하면 OpenAI.
    # 목소리는 이 키의 계정 Voice Library 에 추가돼 있어야 한다. 기본값은 "Sora - Warm & Expressive".
    elevenlabs_api_key: SecretStr = SecretStr("")
    elevenlabs_tts_voice_id: str = "JpGoUJuLFthrFkcdBy4k"
    elevenlabs_tts_model: str = "eleven_multilingual_v2"
    # 숏폼 더빙: 사장님 녹음으로 목소리를 복제해 외국어로 읽힌다. 키가 없으면 OpenAI 목소리.
    elevenlabs_dub_model: str = "eleven_multilingual_v2"
    # 녹음 없이 글(스크립트·가게 정보)로 만든 숏폼을 읽는 목소리. 기본값은 이 계정의 "영천시장 할머니".
    elevenlabs_story_voice_id: str = "OZdqu8aErJPjEj34s1aw"
    # 영상에 넣는 자막 글꼴. 서버(Ubuntu)는 `sudo apt install fonts-noto-cjk` 후 기본값 그대로.
    # Windows 로컬: SUBTITLE_FONT=Malgun Gothic, SUBTITLE_FONT_ZH=Microsoft YaHei. fonts_dir 는 비워 둔다
    # (폴더 안 글꼴을 전부 메모리에 올려 렌더링 하나가 1GB 를 넘는다. 꼭 필요하면 쓸 글꼴 파일만 든 폴더로)
    subtitle_font: str = "Noto Sans CJK KR"
    subtitle_font_zh: str = "Noto Sans CJK SC"
    subtitle_fonts_dir: str = ""
    openai_max_output_tokens: int = 2048
    # 데모용으로 짧게. 실패하면 단계마다 대체 동작이 있으므로 SDK 재시도는 기본 0회.
    openai_timeout: float = 15.0  # 초
    openai_max_retries: int = 0

    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-opus-5-5"
    anthropic_max_tokens: int = 16000
    anthropic_effort: str = "medium"

    # Google Geocoding API (서버 IP 로 제한한 키). 비어 있으면 가게 좌표는 시장 좌표를 쓴다.
    google_maps_api_key: SecretStr = SecretStr("")
    # POST /api/v1/admin/reset 의 X-Admin-Token. 비어 있으면 리셋 엔드포인트가 꺼진다.
    admin_token: SecretStr = SecretStr("")
    # true 면 피드에 시드한 시연 영상만 보인다. 실시간 생성 영상은 /videos/{id} 와 가게 상세에만 나온다.
    feed_demo_only: bool = True
    # 비우면 PATH 의 ffmpeg, 없으면 imageio-ffmpeg 내장 바이너리
    ffmpeg_path: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def media_path(self) -> Path:
        return Path(self.media_dir)

    @property
    def audio_path(self) -> Path:
        return Path(self.audio_dir)


@lru_cache
def get_settings() -> Settings:
    return Settings()
