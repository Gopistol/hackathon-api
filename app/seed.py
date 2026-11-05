"""시드와 리셋. 여러 번 실행해도 같은 결과가 나온다.

    python -m app.seed           # 비어 있을 때만 시드
    python -m app.seed --reset   # DB·생성 파일을 지우고 처음 상태로
"""

import csv
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

from app import seed_data
from app.core.config import get_settings
from app.db.session import Base, SessionLocal, engine, init_db
from app.models import Content, Market, Menu, Store, Video
from app.services.i18n import glossary_translate, save_translations
from app.services.video import ffmpeg_exe, write_vtt

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
MARKETS_CSV = DEMO / "markets.csv"
# 발표용 고퀄 영상. 있으면 임시 영상 대신 이것만 피드에 넣는다.
SHOWCASE = DEMO / "showcase"
DISTRICT = "서대문구"


def _yes(value: str) -> bool:
    return value.strip().upper() in ("Y", "YES", "있음", "O", "1", "TRUE")


def load_markets() -> list[dict]:
    """전국전통시장표준데이터 CSV 에서 서대문구만. 없으면 대체값."""
    if not MARKETS_CSV.exists():
        return [dict(m, has_toilet=False, has_parking=False, vouchers=[]) for m in seed_data.FALLBACK_MARKETS]
    raw = MARKETS_CSV.read_bytes()
    text = next((raw.decode(enc) for enc in ("utf-8-sig", "cp949") if _decodes(raw, enc)), raw.decode("utf-8", "ignore"))
    markets = {}
    for row in csv.DictReader(text.splitlines()):
        address = row.get("소재지도로명주소") or row.get("소재지지번주소") or ""
        if DISTRICT not in address and DISTRICT not in (row.get("소재지지번주소") or ""):
            continue
        try:
            lat, lng = float(row["위도"]), float(row["경도"])
        except (KeyError, ValueError):
            continue
        vouchers = [v.strip() for v in (row.get("사용가능상품권") or "").replace("+", ",").split(",") if v.strip()]
        markets[row["시장명"].strip()] = {
            "name": row["시장명"].strip(),
            "address": address,
            "lat": lat,
            "lng": lng,
            "open_cycle": (row.get("시장개설주기") or "").strip(),
            "has_toilet": _yes(row.get("공중화장실보유여부") or ""),
            "has_parking": _yes(row.get("주차장보유여부") or ""),
            "vouchers": vouchers,
        }
    if not markets:
        logger.warning("CSV 에 %s 시장이 없어 대체값을 씁니다.", DISTRICT)
        return [dict(m, has_toilet=False, has_parking=False, vouchers=[]) for m in seed_data.FALLBACK_MARKETS]
    return list(markets.values())


def _decodes(raw: bytes, enc: str) -> bool:
    try:
        raw.decode(enc)
        return True
    except UnicodeDecodeError:
        return False


def _find_market(markets: list[Market], keyword: str) -> Market:
    return next((m for m in markets if keyword in m.name), markets[0])


def offset(market: Market, index: int) -> tuple[float, float]:
    """같은 시장 가게 핀이 겹치지 않게 시장 좌표 주변에 조금씩 벌린다."""
    dx = ((index % 3) - 1) * 0.00025
    dy = ((index // 3 % 3) - 1) * 0.00025 + 0.0001 * (index // 9)
    return round(market.lat + dy, 6), round(market.lng + dx, 6)


def seed() -> None:
    init_db()
    media = get_settings().media_path
    with SessionLocal() as db:
        if db.scalar(select(Market.id).limit(1)):
            return
        markets = []
        for m in load_markets():
            market = Market(**m, name_i18n=seed_data.MARKET_I18N.get(m["name"], {}))
            db.add(market)
            markets.append(market)
        db.flush()

        per_market: dict[int, int] = {}
        stores: list[tuple[Store, dict]] = []
        for s in seed_data.STORES:
            market = _find_market(markets, s["market"])
            idx = per_market.get(market.id, 0)
            per_market[market.id] = idx + 1
            lat, lng = offset(market, idx)
            store = Store(
                market_id=market.id,
                name=s["name"],
                lat=lat,
                lng=lng,
                payment=s["payment"],
                hours=s["hours"],
                story_ko=s["story_ko"],
                status="published",
            )
            store.menus = [Menu(name_ko=n, price=p, spicy_level=_spicy(n)) for n, p in s["menus"]]
            db.add(store)
            db.flush()
            save_translations(
                db, "store", store.id, {lang: {"name": n, "description": d} for lang, (n, d) in s["i18n"].items()}
            )
            for menu in store.menus:
                save_translations(db, "menu", menu.id, {lang: glossary_translate(menu.name_ko, lang) for lang in ("en", "zh")})
            stores.append((store, s))

        found = showcase_keys()
        known = {s["key"] for s in seed_data.STORES}
        if unknown := found - known:
            logger.warning("demo/showcase 에 가게 key 와 맞지 않는 영상이 있어 건너뜁니다: %s", sorted(unknown))
        # 파일 이름이 틀려 맞는 영상이 하나도 없으면 임시 영상을 그대로 쓴다 (피드가 비지 않게).
        if showcase := found & known:
            # 발표용 영상만 넣는다. 피드는 최신순이라 거꾸로 넣어 STORES 순서대로 보이게 한다.
            for store, s in reversed(stores):
                if s["key"] in showcase:
                    _seed_showcase(db, store, s["key"], media)
        else:
            for store, s in stores:
                _seed_video(db, store, s, media)

        for c in seed_data.CONTENTS:
            db.add(Content(title=c["title"], title_i18n=c["i18n"], platform=c["platform"], genre=c["genre"], foods=c["foods"]))
        db.commit()
    logger.info("시드 완료")


def _spicy(name: str) -> int:
    from app.services.i18n import glossary_entry

    entry = glossary_entry(name)
    return entry["spicy"] if entry else 0


def _seed_video(db, store: Store, s: dict, media: Path) -> None:
    """demo/media 의 미리 렌더링한 영상을 복사하고 자막 VTT 를 만든다. 파일이 없으면 건너뛴다."""
    src_video = DEMO / "media" / "videos" / f"demo-{s['key']}.mp4"
    src_thumb = DEMO / "media" / "thumbs" / f"demo-{s['key']}.jpg"
    if not src_video.exists():
        return
    for src, sub in ((src_video, "videos"), (src_thumb, "thumbs")):
        if src.exists():
            (media / sub).mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, media / sub / src.name)
    duration = 15.0
    subtitles = {}
    for lang, lines in s["script"].items():
        rel = f"subs/demo-{s['key']}.{lang}.vtt"
        write_vtt(lines, duration, media / rel)
        subtitles[lang] = rel
    db.add(
        Video(
            store_id=store.id,
            menu_id=store.menus[0].id,
            job_status="done",
            progress=100,
            video_path=f"videos/{src_video.name}",
            thumbnail_path=f"thumbs/{src_thumb.name}" if src_thumb.exists() else None,
            subtitles=subtitles,
            script=s["script"],
            duration=duration,
            is_demo=True,
        )
    )


def showcase_keys() -> set[str]:
    return {p.stem for p in SHOWCASE.glob("*.mp4")} if SHOWCASE.exists() else set()


def _probe(src: Path, thumb_dst: Path | None) -> float | None:
    """ffmpeg 로 길이(초)를 읽고, thumb_dst 가 있으면 1초 지점 프레임을 썸네일로 저장한다."""
    args = [ffmpeg_exe(), "-hide_banner", "-i", str(src)]
    if thumb_dst:
        thumb_dst.parent.mkdir(parents=True, exist_ok=True)
        args = [ffmpeg_exe(), "-hide_banner", "-y", "-ss", "1", "-i", str(src), "-frames:v", "1", "-vf", "scale=720:-2", str(thumb_dst)]
    out = subprocess.run(args, capture_output=True, text=True, timeout=30).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out)
    return round(int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]), 2) if m else None


def _seed_showcase(db, store: Store, key: str, media: Path) -> None:
    """demo/showcase/<key>.mp4 (+ 선택: <key>.jpg 썸네일, <key>.<lang>.vtt 자막) 을 피드용 영상으로 넣는다."""
    src = SHOWCASE / f"{key}.mp4"
    name = f"showcase-{key}"
    (media / "videos").mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, media / "videos" / f"{name}.mp4")
    thumb = media / "thumbs" / f"{name}.jpg"
    if (SHOWCASE / f"{key}.jpg").exists():
        thumb.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SHOWCASE / f"{key}.jpg", thumb)
        duration = _probe(src, None)
    else:
        duration = _probe(src, thumb)
    subtitles = {}
    for lang in ("ko", "en", "zh"):
        vtt = SHOWCASE / f"{key}.{lang}.vtt"
        if vtt.exists():
            (media / "subs").mkdir(parents=True, exist_ok=True)
            shutil.copy2(vtt, media / "subs" / f"{name}.{lang}.vtt")
            subtitles[lang] = f"subs/{name}.{lang}.vtt"
    db.add(
        Video(
            store_id=store.id,
            menu_id=store.menus[0].id,
            job_status="done",
            progress=100,
            video_path=f"videos/{name}.mp4",
            thumbnail_path=f"thumbs/{name}.jpg" if thumb.exists() else None,
            subtitles=subtitles,
            duration=duration,
            is_demo=True,
        )
    )


def reset() -> None:
    """DB 테이블과 생성 파일(영상·자막·음성)을 지우고 다시 시드한다."""
    s = get_settings()
    Base.metadata.drop_all(bind=engine)
    for d in (s.media_path, s.audio_path, Path(s.audio_dir).parent / "work"):
        shutil.rmtree(d, ignore_errors=True)
    s.media_path.mkdir(parents=True, exist_ok=True)
    seed()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    reset() if "--reset" in sys.argv else seed()
    print("ok")
