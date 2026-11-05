from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    name: Mapped[str] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(300), default=None)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    # cash | card | foreign_card | onnuri
    payment: Mapped[list] = mapped_column(JSON, default=list)
    hours: Mapped[str] = mapped_column(String(200), default="")
    story_ko: Mapped[str] = mapped_column(Text, default="")
    # AI 인터뷰로 등록한 가게: 업종(분식, 건어물 …)과 업종별 정보({"원산지": "국산"})
    category: Mapped[str | None] = mapped_column(String(30), default=None)
    extras: Mapped[dict | None] = mapped_column(JSON, default=None)
    # draft | published
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    market = relationship("Market", lazy="joined")
    menus = relationship("Menu", order_by="Menu.id", cascade="all, delete-orphan", lazy="selectin")


class Menu(Base):
    __tablename__ = "menus"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    name_ko: Mapped[str] = mapped_column(String(100))
    price: Mapped[int | None] = mapped_column(Integer, default=None)
    # 0 안 매움 ~ 3 아주 매움
    spicy_level: Mapped[int] = mapped_column(Integer, default=0)
    # AI 가 추측한 값(가격·맵기 등)이 섞여 있으면 True
    is_ai_guess: Mapped[bool] = mapped_column(Boolean, default=False)
    # 가격 단위 ("100g", "한 봉지"). 없으면 1인분·1개
    unit: Mapped[str | None] = mapped_column(String(30), default=None)


class Translation(Base):
    """가게·메뉴·시장의 언어별 표기. 없으면 한국어 원문을 보여준다."""

    __tablename__ = "translations"
    __table_args__ = (UniqueConstraint("target", "target_id", "lang"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # store | menu
    target: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[int] = mapped_column(Integer, index=True)
    lang: Mapped[str] = mapped_column(String(5))
    name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    how_to_eat: Mapped[str] = mapped_column(Text, default="")
