"""DB 모델 → 응답 dict. 언어별 번역이 없으면 한국어 원문."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.seed_data import FOOD_PHOTOS
from app.models import Market, Menu, Store, Video
from app.services.i18n import load_translations
from app.services.maps import directions_url


def media_url(path: str | None) -> str | None:
    """같은 파일 이름으로 교체해도 브라우저가 옛 파일을 보여주지 않게 수정 시각을 붙인다."""
    if not path:
        return None
    try:
        return f"/media/{path}?v={int((get_settings().media_path / path).stat().st_mtime)}"
    except OSError:
        return f"/media/{path}"


# 메뉴명 원문으로 찾는다. 번역된 이름·메뉴 순번으로 다른 음식 사진을 붙이지 않는다.
MENU_PHOTOS = {
    **FOOD_PHOTOS,
    "기름떡볶이": "oil-tteokbokki",
    "간장떡볶이": "oil-tteokbokki",
    "어묵": "eomuk",
    "찹쌀도넛": "chapssal-doughnuts",
    "식혜": "sikhye",
    "모둠튀김": "twigim",
    "빈대떡": "bindaetteok",
    "고기완자": "wanja",
    "순대국밥": "gukbap",
    "나물반찬": "namul",
}


def menu_photo(name: str) -> str | None:
    photo = MENU_PHOTOS.get("".join(name.split()))
    if not photo or not (get_settings().media_path / f"foods/{photo}.jpg").is_file():
        return None
    return media_url(f"foods/{photo}.jpg")


def store_photo(db: Session, store: Store) -> str | None:
    for menu in store.menus:
        if photo := menu_photo(menu.name_ko):
            return photo
    videos = done_videos(db, store.id)
    return media_url(videos[0].thumbnail_path) if videos else None


def market_photo(db: Session, market_id: int) -> str | None:
    names = db.scalars(
        select(Menu.name_ko).join(Store, Menu.store_id == Store.id)
        .where(Store.market_id == market_id, Store.status == "published")
        .order_by(Store.id, Menu.id)
    )
    return next((photo for name in names if (photo := menu_photo(name))), None)


def market_name(market: Market, lang: str) -> str:
    return market.name if lang == "ko" else market.name_i18n.get(lang) or market.name


def store_name(db: Session, store: Store, lang: str) -> str:
    t = load_translations(db, "store", [store.id], lang).get(store.id)
    return t.name if t and t.name else store.name


def top_menu(db: Session, store: Store, lang: str) -> dict | None:
    if not store.menus:
        return None
    m = store.menus[0]
    t = load_translations(db, "menu", [m.id], lang).get(m.id)
    return {"menu_id": m.id, "name": t.name if t and t.name else m.name_ko, "price": m.price, "spicy_level": m.spicy_level, "image_url": menu_photo(m.name_ko)}


def store_card(db: Session, store: Store, lang: str) -> dict:
    return {
        "store_id": store.id,
        "name": store_name(db, store, lang),
        "market": market_name(store.market, lang),
        "thumbnail_url": store_photo(db, store),
        "lat": store.lat,
        "lng": store.lng,
        "payment": store.payment,
        "top_menu": top_menu(db, store, lang),
        "directions_url": directions_url(store.lat, store.lng),
    }


def video_brief(v: Video, lang: str | None = None) -> dict:
    """lang 이 있으면 그 언어 자막이 들어간 영상을 준다. 없으면(시연 영상 등) 기본 영상."""
    videos = v.videos or {}
    return {
        "video_id": v.id,
        "video_url": media_url(videos.get(lang) or v.video_path),
        "video_urls": {code: media_url(p) for code, p in videos.items()},
        "voice": (v.voices or {}).get(lang) if lang else None,
        "thumbnail_url": media_url(v.thumbnail_path),
        "subtitles": {lang: media_url(p) for lang, p in (v.subtitles or {}).items()},
        "duration": v.duration,
    }


def done_videos(db: Session, store_id: int) -> list[Video]:
    return list(
        db.scalars(select(Video).where(Video.store_id == store_id, Video.job_status == "done").order_by(Video.id.desc()))
    )


def menu_view(m: Menu, t) -> dict:
    return {
        "menu_id": m.id,
        "name": t.name if t and t.name else m.name_ko,
        "name_ko": m.name_ko,
        "image_url": menu_photo(m.name_ko),
        "price": m.price,
        "spicy_level": m.spicy_level,
        "description": t.description if t else "",
        "how_to_eat": t.how_to_eat if t else "",
        "is_ai_guess": m.is_ai_guess,
    }


def market_view(market: Market, lang: str, store_count: int, thumbnail_url: str | None = None) -> dict:
    return {
        "market_id": market.id,
        "name": market_name(market, lang),
        "name_ko": market.name,
        "address": market.address,
        "lat": market.lat,
        "lng": market.lng,
        "open_cycle": market.open_cycle,
        "has_toilet": market.has_toilet,
        "has_parking": market.has_parking,
        "vouchers": market.vouchers,
        "store_count": store_count,
        "thumbnail_url": thumbnail_url,
    }
