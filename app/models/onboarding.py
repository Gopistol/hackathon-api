from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class OnboardingSession(Base):
    """사장님 음성 대화형 등록 세션. 원문 음성 경로와 transcript 를 단계별로 보관한다."""

    __tablename__ = "onboarding_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id"), default=None)
    market_id: Mapped[int | None] = mapped_column(ForeignKey("markets.id"), default=None)
    # store_name → menus → payment → hours → story → done
    step: Mapped[str] = mapped_column(String(20), default="store_name")
    # {step: {transcript, parsed, needs_check, audio_path, confirmed}}
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_audio_path: Mapped[str | None] = mapped_column(String(300), default=None)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
