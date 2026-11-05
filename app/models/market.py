from sqlalchemy import JSON, Boolean, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Market(Base):
    """전통시장. 전국전통시장표준데이터에서 시드한다."""

    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    # {"en": "...", "zh": "..."}
    name_i18n: Mapped[dict] = mapped_column(JSON, default=dict)
    address: Mapped[str] = mapped_column(String(300), default="")
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    open_cycle: Mapped[str] = mapped_column(String(50), default="")
    has_toilet: Mapped[bool] = mapped_column(Boolean, default=False)
    has_parking: Mapped[bool] = mapped_column(Boolean, default=False)
    vouchers: Mapped[list] = mapped_column(JSON, default=list)
