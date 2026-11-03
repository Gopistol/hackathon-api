from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

connect_args: dict = {}
if settings.database_url.startswith("sqlite"):
    # SQLite 파일 위치(예: ./data/app.db)의 디렉터리를 미리 만든다.
    Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False}

# MySQL 은 유휴 연결이 끊기므로 pool_pre_ping 으로 매번 확인한다.
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """해커톤용: 마이그레이션 도구 없이 앱 시작 시 테이블을 만든다."""
    from app import models  # noqa: F401  모델을 등록하기 위해 import

    Base.metadata.create_all(bind=engine)
