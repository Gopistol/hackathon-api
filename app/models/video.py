from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Video(Base):
    """숏폼 영상이자 생성 작업. job_id 와 video_id 는 같은 값이다."""

    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    menu_id: Mapped[int | None] = mapped_column(ForeignKey("menus.id"), default=None)
    # queued | processing | done | failed
    job_status: Mapped[str] = mapped_column(String(20), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(300), default=None)
    # RECORDING_TOO_LONG | VIDEO_FAILED (프론트가 다시 녹음 / 다시 시도를 구분하는 용도)
    error_code: Mapped[str | None] = mapped_column(String(30), default=None)
    # media_dir 기준 상대경로
    video_path: Mapped[str | None] = mapped_column(String(300), default=None)
    thumbnail_path: Mapped[str | None] = mapped_column(String(300), default=None)
    # {"en": "subs/1.en.vtt", ...}
    subtitles: Mapped[dict] = mapped_column(JSON, default=dict)
    # {"ko": [...], "en": [...]} 자막 원문
    script: Mapped[dict] = mapped_column(JSON, default=dict)
    # script(직접 쓴 스크립트) | stt(녹음) | auto(가게 기본 정보)
    script_source: Mapped[str | None] = mapped_column(String(10), default=None)
    duration: Mapped[float | None] = mapped_column(Float, default=None)
    # 시드로 넣은 미리 렌더링 영상
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
