from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Content(Base):
    """K-콘텐츠(P1). 수동 시드, 텍스트 카드만 쓴다."""

    __tablename__ = "contents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    # {"en": "...", "zh": "..."}
    title_i18n: Mapped[dict] = mapped_column(JSON, default=dict)
    platform: Mapped[str] = mapped_column(String(50), default="")
    genre: Mapped[str] = mapped_column(String(50), default="")
    # 작품에 나온 음식 한국어 이름: ["떡볶이", "순대"]
    foods: Mapped[list] = mapped_column(JSON, default=list)
    # media_dir 기준 상대경로. 직접 만든 카드 이미지 (포스터·스틸컷 아님)
    image_path: Mapped[str | None] = mapped_column(String(300), default=None)
