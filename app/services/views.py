"""DB 모델 → 응답 dict. 언어별 번역이 없으면 한국어 원문."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Market, Menu, Store, Video
from app.services.i18n import load_translations
from app.services.maps import directions_url


def media_url(path: str | None) -> str | None:
    return f"/media/{path}" if path else None


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
    return {"menu_id": m.id, "name": t.name if t and t.name else m.name_ko, "price": m.price, "spicy_level": m.spicy_level}


def store_card(db: Session, store: Store, lang: str) -> dict:
    return {
        "store_id": store.id,
        "name": store_name(db, store, lang),
        "market": market_name(store.market, lang),
        "lat": store.lat,
        "lng": store.lng,
        "payment": store.payment,
        "top_menu": top_menu(db, store, lang),
        "directions_url": directions_url(store.lat, store.lng),
    }


def video_brief(v: Video) -> dict:
    return {
        "video_id": v.id,
        "video_url": media_url(v.video_path),
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
        "price": m.price,
        "spicy_level": m.spicy_level,
        "description": t.description if t else "",
        "how_to_eat": t.how_to_eat if t else "",
        "is_ai_guess": m.is_ai_guess,
    }


def market_view(market: Market, lang: str, store_count: int) -> dict:
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
    }
