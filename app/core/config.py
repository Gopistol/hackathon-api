from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수 설정. 로컬은 .env, 서버는 /etc/hackathon/hackathon.env (systemd EnvironmentFile)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Hackathon API"
    # nip.io 의 /hack 경로 방식으로 붙일 때만 "/hack" 로 설정한다.
    root_path: str = ""
    # 콤마로 구분한 프론트 주소. 프론트 배포 주소가 정해지면 여기에 추가한다.
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # 기본은 SQLite. MySQL 을 쓰면 mysql+pymysql://hackathon:<pw>@127.0.0.1:3306/hackathon
    database_url: str = "sqlite:///./data/app.db"

    # MinIO(S3 호환). 비어 있으면 스토리지 기능이 꺼진다.
    s3_endpoint_url: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "hackathon"
    s3_region: str = "us-east-1"

    # LLM: openai | anthropic | mock. 선택한 프로바이더의 키가 비어 있으면 mock 으로 동작한다.
    # 기본값이 openai 이므로 OPENAI_API_KEY 만 넣으면 실제 호출로 바뀐다.
    llm_provider: str = "openai"

    # OpenAI. SecretStr 이라 설정 객체를 출력·로그해도 키가 가려진다.
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4.1-mini"
    openai_max_output_tokens: int = 1024
    openai_timeout: float = 30.0  # 초. 재시도 1회 포함 최악 약 2배

    # Anthropic (선택)
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-opus-5-5"
    anthropic_max_tokens: int = 16000
    anthropic_effort: str = "medium"  # low | medium | high | xhigh | max

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
