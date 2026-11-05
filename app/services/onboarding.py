"""사장님 음성 대화형 등록: store_name → menus → payment → hours → story → done → publish"""

import asyncio
import json
import logging
import time

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, not_found
from app.db.session import SessionLocal
from app.llm import LLMProvider
from app.models import Market, Menu, OnboardingSession, Store
from app.services import ai
from app.services.i18n import save_translations
from app.services.maps import geocode
from app.seed import offset

logger = logging.getLogger(__name__)

DONE = "done"


def question(step: str) -> dict:
    q, ex = ai.QUESTIONS[step]
    return {"step": step, "question_ko": q, "example_ko": ex}


def get_session(db: Session, session_id: int) -> OnboardingSession:
    s = db.get(OnboardingSession, session_id)
    if not s:
        raise not_found("등록 세션")
    return s


def start(db: Session, store_id: int | None, market_id: int | None) -> OnboardingSession:
    if store_id is not None:
        store = db.get(Store, store_id)
        if not store:
            raise not_found("가게")
        market_id = store.market_id
    if market_id is not None and not db.get(Market, market_id):
        raise not_found("시장")
    if market_id is None:
        first = db.query(Market).order_by(Market.id).first()
        if not first:
            raise AppError("NO_MARKET", "시장 데이터가 없습니다. 시드를 먼저 실행하세요.", 409)
        market_id = first.id
    s = OnboardingSession(store_id=store_id, market_id=market_id, step="store_name", answers={})
    db.add(s)
    db.commit()
    return s


def _save_audio(session_id: int, step: str, data: bytes, filename: str) -> str:
    path = get_settings().audio_path / "sessions" / str(session_id) / f"{step}-{int(time.time() * 1000)}.{ai.audio_ext(data)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


async def answer(
    db: Session, llm: LLMProvider, s: OnboardingSession, audio: bytes | None, filename: str, text: str | None
) -> dict:
    if s.step == DONE:
        raise AppError("ONBOARDING_DONE", "모든 질문에 답했어요. 요약을 확인하고 올려 주세요.", 409)
    step = s.step
    audio_path = None
    if audio:
        audio_path = _save_audio(s.id, step, audio, filename)
        s.raw_audio_path = audio_path
        transcript = await ai.transcribe(llm, audio, filename, step)
    else:
        transcript = (text or "").strip()
    parsed, needs = await ai.structure_step(llm, step, transcript)
    # JSON 컬럼은 새 dict 를 대입해야 변경이 저장된다.
    s.answers = {
        **s.answers,
        step: {"transcript": transcript, "parsed": parsed, "needs_check": needs, "audio_path": audio_path, "confirmed": False},
    }
    db.commit()
    return {
        "session_id": s.id,
        "step": step,
        "transcript": transcript,
        "parsed": parsed,
        "needs_check": needs,
        "confirm_text_ko": ai.confirm_text(step, parsed),
        "mode": llm.name,
    }


def confirm(db: Session, s: OnboardingSession, step: str, accepted: bool, edits: dict | None) -> dict:
    editing_completed = s.step == DONE and not s.published and bool(edits) and step in s.answers
    if step != s.step and not editing_completed:
        raise AppError("STEP_MISMATCH", f"지금 단계는 '{s.step}' 입니다.", 409)
    entry = s.answers.get(step)
    if not entry and not edits:
        raise AppError("NO_ANSWER", "먼저 답변을 보내 주세요.", 409)
    if edits:
        base = entry["parsed"] if entry else {}
        try:
            parsed = ai.normalize(step, {**base, **edits})
        except (ValueError, TypeError, AttributeError) as e:
            raise AppError("VALIDATION_ERROR", "수정한 값의 형식이 올바르지 않아요.", 422) from e
        entry = {**(entry or {"transcript": "", "audio_path": None}), "parsed": parsed, "needs_check": []}
        accepted = True  # 사장님이 직접 고친 값은 확정으로 본다
    if not accepted:
        return {"session_id": s.id, "done": False, "retry": True, **question(step)}

    s.answers = {**s.answers, step: {**entry, "confirmed": True}}
    if not editing_completed:
        idx = ai.STEPS.index(step)
        s.step = ai.STEPS[idx + 1] if idx + 1 < len(ai.STEPS) else DONE
    db.commit()
    if s.step == DONE:
        return {"session_id": s.id, "done": True, "retry": False, "step": DONE, "question_ko": None, "example_ko": None}
    return {"session_id": s.id, "done": False, "retry": False, **question(s.step)}


def _parsed(s: OnboardingSession, step: str) -> dict:
    return (s.answers.get(step) or {}).get("parsed") or {}


def _translation_input(s: OnboardingSession) -> tuple[str, str, list[dict]]:
    menus = [
        {"id": f"m{i}", "name_ko": m["name_ko"], "price": m["price"], "spicy_level": m["spicy_level"]}
        for i, m in enumerate(_parsed(s, "menus").get("menus", []))
    ]
    return _parsed(s, "store_name").get("name_ko", ""), _parsed(s, "story").get("story_ko", ""), menus


def _cache_key(s: OnboardingSession) -> str:
    return json.dumps(_translation_input(s), ensure_ascii=False, sort_keys=True)


def _cached_translations(s: OnboardingSession) -> dict | None:
    cached = s.answers.get("_translations")
    return cached["data"] if cached and cached.get("key") == _cache_key(s) else None


async def _translations(db: Session, llm: LLMProvider, s: OnboardingSession) -> dict:
    """올리기에서 쓰는 번역. 미리 해 둔 번역(prefetch)이 있으면 다시 부르지 않는다."""
    if (data := _cached_translations(s)) is not None:
        return data
    name, story, menus = _translation_input(s)
    data = await ai.translate_store(llm, name, story, menus)
    s.answers = {**s.answers, "_translations": {"key": _cache_key(s), "data": data}}
    db.commit()
    return data


# 진행 중인 미리 번역. 올리기가 같은 번역을 또 부르지 않고 이걸 기다린다.
_prefetching: dict[int, asyncio.Event] = {}
PREFETCH_WAIT = 15.0  # 초


async def prefetch_translations(session_id: int, llm: LLMProvider) -> None:
    """마지막 단계를 확인하면 백그라운드로 번역을 미리 해 둔다. 요약·올리기가 기다리지 않게."""
    done = _prefetching.setdefault(session_id, asyncio.Event())
    try:
        with SessionLocal() as db:
            s = db.get(OnboardingSession, session_id)
            if s and not s.published:
                await _translations(db, llm, s)
    except Exception as e:  # 실패하면 올리기 때 다시 시도한다
        logger.warning("번역 미리 하기 실패 session=%s: %s", session_id, type(e).__name__)
    finally:
        done.set()
        _prefetching.pop(session_id, None)


async def _wait_prefetch(db: Session, s: OnboardingSession) -> None:
    if (done := _prefetching.get(s.id)) is not None:
        try:
            await asyncio.wait_for(done.wait(), PREFETCH_WAIT)
        except TimeoutError:
            pass
        db.refresh(s)  # 다른 세션에서 저장한 번역 캐시를 읽는다


def _preview(s: OnboardingSession, lang: str, cached: dict | None, name: str, story: str, menus: list[dict]) -> dict | None:
    if not name:
        return None
    t = cached[lang] if cached else ai.fallback_translation(name, story, menus, lang)
    return {
        "translated": cached is not None,  # false 면 잠시 뒤 다시 요약을 부르면 번역본
        "name": t["store"]["name"],
        "story": t["store"]["description"],
        "payment": _parsed(s, "payment").get("payment", []),
        "hours": _parsed(s, "hours").get("hours", ""),
        "menus": [
            {"name": t["menus"][m["id"]]["name"], "price": m["price"], "spicy_level": m["spicy_level"],
             "description": t["menus"][m["id"]]["description"], "how_to_eat": t["menus"][m["id"]]["how_to_eat"]}
            for m in menus
        ],
    }  # fmt: skip


async def summary(db: Session, llm: LLMProvider, s: OnboardingSession) -> dict:
    steps = {
        step: {k: (s.answers.get(step) or {}).get(k) for k in ("transcript", "parsed", "needs_check", "confirmed")}
        for step in ai.STEPS
    }
    needs = [n for step in ai.STEPS for n in ((s.answers.get(step) or {}).get("needs_check") or [])]
    name, story, menus = _translation_input(s)
    # 요약은 번역을 기다리지 않는다. 미리 해 둔 번역이 없으면 음식 용어집으로 미리보기를 만든다.
    cached = _cached_translations(s) if name else None
    return {
        "session_id": s.id,
        "step": s.step,
        "done": s.step == DONE,
        "store_id": s.store_id,
        "steps": steps,
        "needs_check": needs,
        "preview_en": _preview(s, "en", cached, name, story, menus),
        "preview_zh": _preview(s, "zh", cached, name, story, menus),
        "mode": llm.name,
    }


async def publish(db: Session, llm: LLMProvider, s: OnboardingSession) -> dict:
    if s.step != DONE:
        raise AppError("ONBOARDING_INCOMPLETE", "아직 확인하지 않은 질문이 있어요.", 409)
    if s.published and s.store_id:
        return {"store_id": s.store_id, "status": "published", "mode": llm.name}

    name = _parsed(s, "store_name")
    menus = _parsed(s, "menus").get("menus", [])
    await _wait_prefetch(db, s)
    translations = await _translations(db, llm, s)

    store = db.get(Store, s.store_id) if s.store_id else None
    market = db.get(Market, s.market_id)
    if store is None:
        store = Store(market_id=market.id, lat=market.lat, lng=market.lng)
        db.add(store)
    store.name = name.get("name_ko") or "이름 없는 가게"
    store.payment = _parsed(s, "payment").get("payment", [])
    store.hours = _parsed(s, "hours").get("hours", "")
    store.story_ko = _parsed(s, "story").get("story_ko", "")
    store.status = "published"
    store.menus = [
        Menu(name_ko=m["name_ko"], price=m["price"], spicy_level=m["spicy_level"], is_ai_guess=m["is_ai_guess"])
        for m in menus
    ]
    db.flush()

    coords = await geocode(store.address) if store.address else None
    if coords:
        store.lat, store.lng = coords
    elif not s.store_id:
        count = db.query(Store).filter(Store.market_id == market.id, Store.id != store.id).count()
        store.lat, store.lng = offset(market, count)

    save_translations(db, "store", store.id, {lang: t["store"] for lang, t in translations.items()})
    for i, menu in enumerate(store.menus):
        save_translations(db, "menu", menu.id, {lang: t["menus"][f"m{i}"] for lang, t in translations.items()})

    s.store_id, s.published = store.id, True
    db.commit()
    return {"store_id": store.id, "status": "published", "mode": llm.name}


async def retranslate(db: Session, llm: LLMProvider, store: Store) -> None:
    """가게·메뉴 수정 후 번역 재생성 (P1)."""
    menus = [{"id": f"m{m.id}", "name_ko": m.name_ko, "price": m.price, "spicy_level": m.spicy_level} for m in store.menus]
    data = await ai.translate_store(llm, store.name, store.story_ko, menus)
    save_translations(db, "store", store.id, {lang: t["store"] for lang, t in data.items()})
    for m in store.menus:
        save_translations(db, "menu", m.id, {lang: t["menus"][f"m{m.id}"] for lang, t in data.items()})
    db.commit()
