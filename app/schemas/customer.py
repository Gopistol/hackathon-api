from pydantic import BaseModel, Field

from app.services.i18n import Lang


class TopMenu(BaseModel):
    menu_id: int
    name: str
    price: int | None
    spicy_level: int


class MarketOut(BaseModel):
    market_id: int
    name: str
    name_ko: str
    address: str
    lat: float
    lng: float
    open_cycle: str
    has_toilet: bool
    has_parking: bool
    vouchers: list[str]
    store_count: int


class PriceRange(BaseModel):
    min: int | None
    max: int | None


class StorePin(BaseModel):
    store_id: int
    name: str
    lat: float
    lng: float
    top_menu: TopMenu | None
    price_range: PriceRange
    thumbnail_url: str | None
    directions_url: str


class MarketStoresOut(BaseModel):
    market: MarketOut
    stores: list[StorePin]


class StoreCard(BaseModel):
    store_id: int
    name: str
    market: str
    lat: float
    lng: float
    payment: list[str] = Field(description="cash | card | foreign_card | onnuri")
    top_menu: TopMenu | None
    directions_url: str


class FeedItem(BaseModel):
    video_id: int
    video_url: str
    thumbnail_url: str | None
    subtitles: dict[str, str] = Field(description="언어별 VTT URL")
    duration: float | None
    store: StoreCard


class FeedOut(BaseModel):
    items: list[FeedItem]
    next_cursor: int | None = Field(description="다음 페이지 요청 시 cursor 로 보낸다. null 이면 끝")


class MenuOut(BaseModel):
    menu_id: int
    name: str
    name_ko: str
    price: int | None
    spicy_level: int
    description: str
    how_to_eat: str
    is_ai_guess: bool


class VideoBrief(BaseModel):
    video_id: int
    video_url: str
    thumbnail_url: str | None
    subtitles: dict[str, str]
    duration: float | None


class StoreDetail(BaseModel):
    store_id: int
    lang: Lang
    name: str
    name_ko: str
    market: dict = Field(examples=[{"market_id": 1, "name": "Dongnimmun Yeongcheon Market"}])
    address: str | None
    lat: float
    lng: float
    payment: list[str]
    hours: str
    story: str
    story_ko: str
    menus: list[MenuOut]
    videos: list[VideoBrief]
    directions_url: str
    status: str


class OrderItem(BaseModel):
    menu_id: int
    qty: int = Field(ge=1, le=50)


class OrderPreviewIn(BaseModel):
    store_id: int
    items: list[OrderItem] = Field(min_length=1, max_length=30)
    lang: Lang = "en"

    model_config = {"json_schema_extra": {"examples": [{"store_id": 1, "items": [{"menu_id": 1, "qty": 2}, {"menu_id": 3, "qty": 1}], "lang": "en"}]}}


class OrderPreviewOut(BaseModel):
    ko_message: str = Field(examples=["기름떡볶이 2개, 순대 1개 주세요."])
    ko_lines: list[str] = Field(examples=[["기름떡볶이 2개 8,000원", "순대 1개 5,000원"]])
    total_price: int
    translated_lines: list[str] = Field(examples=[["Oil Tteokbokki x2 ₩8,000", "Sundae (Korean Sausage) x1 ₩5,000"]])
    lang: Lang


class VideoDetail(VideoBrief):
    store_id: int
    menu: TopMenu | None
    status: str
    script_ko: list[str] = Field(description="한국어 자막 줄")
    script_source: str | None = Field(description="script(직접 쓴 스크립트) | stt(녹음) | auto(가게 기본 정보)")


class ContentOut(BaseModel):
    content_id: int
    title: str
    title_ko: str
    platform: str
    genre: str
    foods: list[str]
    foods_ko: list[str]
    image_url: str | None = Field(description="카드 이미지 (직접 만든 더미 이미지, 포스터·스틸컷 아님)")


class FoodMatch(BaseModel):
    food_ko: str
    name: str
    stores: list[StoreCard]


class ContentFoodsOut(BaseModel):
    content_id: int
    title: str
    image_url: str | None
    foods: list[dict] = Field(
        description="[{food_ko, name, stores: [StoreCard], similar: [FoodMatch]}]. 매칭 가게가 없으면 similar 에 비슷한 음식"
    )
