"""손님 API: 시장·지도, 피드, 가게 상세, 주문 화면, 영상 상세, K-콘텐츠(P1)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ERROR_RESPONSES, AppError, not_found
from app.db.session import get_db
from app.models import Content, Market, Menu, Store, Video
from app.schemas.customer import (
    ContentFoodsOut,
    ContentOut,
    FeedOut,
    MarketOut,
    MarketStoresOut,
    OrderPreviewIn,
    OrderPreviewOut,
    StoreDetail,
    StorePin,
    VideoDetail,
)
from app.services.i18n import SIMILAR_FOODS, Lang, food_name, load_translations
from app.services.maps import directions_url
from app.services.views import (
    done_videos,
    market_name,
    market_view,
    media_url,
    menu_view,
    store_card,
    store_name,
    top_menu,
    video_brief,
)

router = APIRouter(tags=["customer"], responses=ERROR_RESPONSES)

LangQ = Query("en", description="ko | en | zh")
FEED_PAGE = 10


def _published(db: Session, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if not store or store.status != "published":
        raise not_found("가게")
    return store


@router.get("/markets", response_model=list[MarketOut], summary="시장 목록")
def list_markets(lang: Lang = LangQ, db: Session = Depends(get_db)):
    counts = dict(
        db.execute(select(Store.market_id, func.count()).where(Store.status == "published").group_by(Store.market_id)).all()
    )
    markets = db.scalars(select(Market).order_by(Market.id))
    return [market_view(m, lang, counts.get(m.id, 0)) for m in markets]


@router.get("/markets/{market_id}/stores", response_model=MarketStoresOut, summary="시장 내 가게 핀")
def market_stores(market_id: int, lang: Lang = LangQ, db: Session = Depends(get_db)):
    market = db.get(Market, market_id)
    if not market:
        raise not_found("시장")
    stores = list(
        db.scalars(select(Store).where(Store.market_id == market_id, Store.status == "published").order_by(Store.id))
    )
    pins = []
    for s in stores:
        prices = [m.price for m in s.menus if m.price is not None]
        videos = done_videos(db, s.id)
        pins.append(
            {
                "store_id": s.id,
                "name": store_name(db, s, lang),
                "lat": s.lat,
                "lng": s.lng,
                "top_menu": top_menu(db, s, lang),
                "price_range": {"min": min(prices, default=None), "max": max(prices, default=None)},
                "thumbnail_url": media_url(videos[0].thumbnail_path) if videos else None,
                "directions_url": directions_url(s.lat, s.lng),
            }
        )
    return {"market": market_view(market, lang, len(stores)), "stores": pins}


@router.get("/feed", response_model=FeedOut, summary="숏폼 피드")
def feed(lang: Lang = LangQ, cursor: int | None = Query(None, description="이전 응답의 next_cursor"), db: Session = Depends(get_db)):
    q = (
        select(Video)
        .join(Store, Store.id == Video.store_id)
        .where(Video.job_status == "done", Store.status == "published")
        .order_by(Video.id.desc())
        .limit(FEED_PAGE + 1)
    )
    if cursor is not None:
        q = q.where(Video.id < cursor)
    if get_settings().feed_demo_only:
        q = q.where(Video.is_demo.is_(True))
    videos = list(db.scalars(q))
    has_more = len(videos) > FEED_PAGE
    videos = videos[:FEED_PAGE]
    items = [{**video_brief(v, lang), "store": store_card(db, db.get(Store, v.store_id), lang)} for v in videos]
    return {"items": items, "next_cursor": videos[-1].id if has_more else None}


@router.get("/stores/{store_id}", response_model=StoreDetail, summary="가게 상세")
def store_detail(store_id: int, lang: Lang = LangQ, db: Session = Depends(get_db)):
    store = _published(db, store_id)
    st = load_translations(db, "store", [store.id], lang).get(store.id)
    mt = load_translations(db, "menu", [m.id for m in store.menus], lang)
    return {
        "store_id": store.id,
        "lang": lang,
        "name": st.name if st and st.name else store.name,
        "name_ko": store.name,
        "market": {"market_id": store.market_id, "name": market_name(store.market, lang)},
        "address": store.address,
        "lat": store.lat,
        "lng": store.lng,
        "payment": store.payment,
        "hours": store.hours,
        "story": st.description if st and st.description else store.story_ko,
        "story_ko": store.story_ko,
        "menus": [menu_view(m, mt.get(m.id)) for m in store.menus],
        "videos": [video_brief(v, lang) for v in done_videos(db, store.id)],
        "directions_url": directions_url(store.lat, store.lng),
        "status": store.status,
    }


@router.post("/orders/preview", response_model=OrderPreviewOut, summary="주문 화면 생성 (한국어 문장)")
def order_preview(body: OrderPreviewIn, db: Session = Depends(get_db)):
    store = _published(db, body.store_id)
    menus = {m.id: m for m in store.menus}
    tr = load_translations(db, "menu", list(menus), body.lang)
    ko_parts, ko_lines, translated, total = [], [], [], 0
    for item in body.items:
        m = menus.get(item.menu_id)
        if not m:
            raise not_found(f"메뉴(id={item.menu_id})")
        # 일부 금액만 합계처럼 보이지 않도록 가격 미정 메뉴는 주문 화면을 만들지 않는다.
        if m.price is None:
            raise AppError("PRICE_UNKNOWN", f"'{m.name_ko}'은(는) 가격이 정해지지 않아 주문 화면을 만들 수 없어요.", 409)
        name = tr[m.id].name if m.id in tr and tr[m.id].name else m.name_ko
        subtotal = m.price * item.qty
        total += subtotal
        ko_parts.append(f"{m.name_ko} {item.qty}개")
        ko_lines.append(f"{m.name_ko} {item.qty}개 {subtotal:,}원")
        translated.append(f"{name} x{item.qty} ₩{subtotal:,}")
    return {
        "ko_message": f"{', '.join(ko_parts)} 주세요.",
        "ko_lines": ko_lines,
        "total_price": total,
        "translated_lines": translated,
        "lang": body.lang,
    }


@router.get("/videos/{video_id}", response_model=VideoDetail, summary="영상 상세")
def video_detail(video_id: int, lang: Lang = LangQ, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v or v.job_status != "done":
        raise not_found("영상")
    menu = None
    if v.menu_id and (m := db.get(Menu, v.menu_id)):
        t = load_translations(db, "menu", [m.id], lang).get(m.id)
        menu = {"menu_id": m.id, "name": t.name if t and t.name else m.name_ko, "price": m.price, "spicy_level": m.spicy_level}
    return {
        **video_brief(v, lang),
        "store_id": v.store_id,
        "menu": menu,
        "status": v.job_status,
        "script_ko": (v.script or {}).get("ko", []),
        "script_source": v.script_source,
    }


# ---------- P1: K-콘텐츠 ----------


@router.get("/contents", response_model=list[ContentOut], summary="K-콘텐츠 목록 (P1)")
def list_contents(lang: Lang = LangQ, db: Session = Depends(get_db)):
    return [
        {
            "content_id": c.id,
            "title": c.title if lang == "ko" else c.title_i18n.get(lang) or c.title,
            "title_ko": c.title,
            "platform": c.platform,
            "genre": c.genre,
            "foods": [food_name(f, lang) for f in c.foods],
            "foods_ko": c.foods,
            "image_url": media_url(c.image_path),
        }
        for c in db.scalars(select(Content).order_by(Content.id))
    ]


def _stores_selling(db: Session, food_ko: str, lang: str) -> list[dict]:
    stores = db.scalars(
        select(Store)
        .join(Menu, Menu.store_id == Store.id)
        .where(Store.status == "published", Menu.name_ko.contains(food_ko))
        .distinct()
        .order_by(Store.id)
    )
    return [store_card(db, s, lang) for s in stores]


@router.get("/contents/{content_id}/foods", response_model=ContentFoodsOut, summary="작품 음식 → 가게 (P1)")
def content_foods(content_id: int, lang: Lang = LangQ, db: Session = Depends(get_db)):
    c = db.get(Content, content_id)
    if not c:
        raise not_found("작품")
    foods = []
    for f in c.foods:
        stores = _stores_selling(db, f, lang)
        similar = []
        if not stores:
            for alt in SIMILAR_FOODS.get(f, []):
                if alt_stores := _stores_selling(db, alt, lang):
                    similar.append({"food_ko": alt, "name": food_name(alt, lang), "stores": alt_stores})
        foods.append({"food_ko": f, "name": food_name(f, lang), "stores": stores, "similar": similar})
    return {
        "content_id": c.id,
        "title": c.title if lang == "ko" else c.title_i18n.get(lang) or c.title,
        "image_url": media_url(c.image_path),
        "foods": foods,
    }
