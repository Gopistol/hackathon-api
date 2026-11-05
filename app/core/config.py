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
    openai_max_output_tokens: int = 2048
    openai_timeout: float = 30.0  # 초. 재시도 1회 포함 최악 약 2배

    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-opus-5-5"
    anthropic_max_tokens: int = 16000
    anthropic_effort: str = "medium"

    # Google Geocoding API (서버 IP 로 제한한 키). 비어 있으면 가게 좌표는 시장 좌표를 쓴다.
    google_maps_api_key: SecretStr = SecretStr("")
    # POST /api/v1/admin/reset 의 X-Admin-Token. 비어 있으면 리셋 엔드포인트가 꺼진다.
    admin_token: SecretStr = SecretStr("")
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
