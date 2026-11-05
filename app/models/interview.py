from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Interview(Base):
    """통화처럼 이어지는 AI 인터뷰. register(가게 정보 등록) | story(숏폼용 사장님 이야기)."""

    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id"), default=None)
    market_id: Mapped[int | None] = mapped_column(ForeignKey("markets.id"), default=None)
    # active | done | published
    status: Mapped[str] = mapped_column(String(10), default="active")
    # [{question, transcript, audio_path}]
    turns: Mapped[list] = mapped_column(JSON, default=list)
    # register: {draft, needs_check, asking, extra_asked} / story: {covered, anecdote, asking}
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    # register: {session_id} / story: {quotes: [{turn, text}], script_ko}
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
