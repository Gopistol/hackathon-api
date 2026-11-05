"""통화처럼 이어지는 AI 인터뷰.

브라우저: AI 음성 재생 → 자동으로 듣기 → 말이 멈추면 보냄 → 다음 AI 음성 … (버튼 없이 이어진다)
서버: 한 턴에 음성 인식 1번 + LLM 1번. 다음 질문과 종료는 LLM 이 제안하고 서버 규칙으로 확정한다.
- register: 업종에 맞춰 가게 정보를 묻는다. 끝나면 기존 등록 세션(done)으로 넘겨 요약·올리기를 그대로 쓴다.
- story: 사장님 이야기를 끌어낸다. 끝나면 원문에서 그대로 고른 구간(quotes)으로 30초 이야기를 만든다.
LLM 이 실패하거나 mock 이면 고정 질문 + 규칙 기반 정리로 같은 흐름을 이어 간다.
"""

import asyncio
import difflib
import json
import logging
import re
import time

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, not_found
from app.db.session import SessionLocal
from app.llm import LLMError, LLMProvider
from app.models import Interview, Market, OnboardingSession, Store
from app.prompts import (
    CATEGORY_PROFILES,
    REGISTER_INTERVIEW_PROMPT,
    STORY_BEATS,
    STORY_COMPOSE_PROMPT,
    STORY_INTERVIEW_PROMPT,
)
from app.services import ai, onboarding, tts
from app.services.i18n import PAYMENTS, PAYMENT_KO

logger = logging.getLogger(__name__)

REQUIRED = ["store_name", "category", "items", "payment", "hours"]
MAX_EXTRA = 2  # 필수 항목 다음에 묻는 업종별 질문 수
MAX_TURNS = {"register": 12, "story": 10}
MIN_STORY_TURNS = 4
MAX_ASK_PER_BEAT = 2
MAX_ASK_PER_FIELD = 2  # 가게 등록도 한 항목은 두 번까지만 묻고, 못 들으면 요약 화면 확인 목록으로 넘긴다
STT_BUDGET = 6.0  # 초. 음성 인식이 늦으면 브라우저가 받아 적은 글을 쓰거나 다시 묻는다 (오래 조용하지 않게)
TURN_BUDGET = 8.0  # 초. 넘으면 고정 질문으로 이어 간다 (통화가 끊긴 느낌이 들지 않게)
TURN_MAX_TOKENS = 400  # 바뀐 항목과 할 말만 받는다 (출력이 짧을수록 빨리 온다)
COMPOSE_BUDGET = 15.0
HISTORY = 12  # 지난 질문·답을 전부 넘겨 맥락을 잃지 않게 한다 (최대 턴 수 이상)
QUOTE_CHARS = (40, 170)  # 고른 구간 글자 수 (너무 짧으면 규칙으로, 길면 자른다)

_STOP_RE = re.compile(r"그만\s*(할게|하자|해요|합시다|하고)|그게\s*다(예요|에요|야|입니다)|이제\s*됐|다\s*했어|끝낼게")

CLOSING = {
    "register": "고맙습니다! 말씀하신 내용을 화면에 정리해 드릴게요.",
    "story": "귀한 이야기 고맙습니다. 이 이야기로 영상을 만들어 드릴게요.",
}
REPROMPT = "잘 못 들었어요. 한 번만 더 말씀해 주세요."
# 연달아 못 알아들으면 말하는 방법을 알려 준다
REPROMPT_AGAIN = "잘 안 들려요. 휴대폰을 입 가까이 대고 천천히 말씀해 주세요."
# 말이 끝나자마자 프론트가 바로 틀어 주는 짧은 맞장구 (다음 질문을 기다리는 동안 조용하지 않게).
# "네~"처럼 너무 짧으면 음성 합성이 다른 나라 말처럼 읽는 경우가 있어 한 마디 이상으로 둔다.
FILLERS = ["네, 잘 들었어요.", "아, 그러시구나.", "네, 알겠어요."]
# 맞장구를 이미 틀었으니 AI 말 앞의 "네," "아," 는 뺀다 (같은 말이 두 번 들리지 않게)
_CONFIRM_RE = re.compile(r"(맞(나요|죠|으세요|습니까|아요|지요)|하신\s*거죠|이신\s*거죠|이시죠)\s*\??\s*$")
_LEAD_FILLER = re.compile(r"^(네|예|아|음|아하)[,.~!]*\s+")

REGISTER_QUESTIONS = {
    "store_name": "안녕하세요! 가게 등록을 도와드릴게요. 가게 이름이 뭐예요?",
    "category": "어떤 가게예요? 분식집, 건어물집처럼 말씀해 주세요.",
    "payment": "결제는 어떻게 받으세요? 현금, 카드, 해외카드, 온누리상품권 중에 말씀해 주세요.",
    "hours": "몇 시부터 몇 시까지 하세요? 쉬는 날도 알려 주세요.",
}
STORY_QUESTIONS = {
    "origin": "이 장사는 언제, 어떻게 시작하셨어요?",
    "years": "처음 장사하실 때 이 시장이랑 동네는 어떤 모습이었어요?",
    "craft": "장사하시면서 이것만은 꼭 지킨다, 하는 게 있으세요?",
    "people": "기억에 남는 손님 한 분만 얘기해 주세요.",
    "message": "마지막으로, 외국에서 온 손님들께 한마디 해 주세요.",
}

# mock 모드에서 음성 대신 쓰는 사장님 답 (지금 묻는 주제별)
MOCK_ANSWERS = {
    "store_name": "포방터 건어물이에요",
    "category": "건어물 가게예요",
    "items": "멸치 5천원, 오징어 3천원이요",
    "payment": "현금이랑 카드 돼요",
    "hours": "아침 9시부터 저녁 8시까지 해요",
    "origin": "1985년에 시어머니한테 물려받아서 시작했어요. 그때는 리어카 하나였어요.",
    "years": "옛날엔 시장에 사람이 바글바글했어요. 요즘은 외국 손님이 많이 와요.",
    "craft": "멸치는 매일 아침 직접 골라요. 그건 40년 동안 안 바꿨어요.",
    "people": "일본 손님이 10년 만에 다시 와서 그 멸치 아직 있냐고 물어보셨어요.",
    "message": "한국 시장 멸치 맛 꼭 보고 가세요.",
}


# ---------- 공통 ----------


def get(db: Session, interview_id: int) -> Interview:
    iv = db.get(Interview, interview_id)
    if not iv:
        raise not_found("인터뷰")
    return iv


def _rang(word: str) -> str:
    """'메뉴랑' / '상품이랑' (받침에 따라)."""
    last = word[-1] if word else "가"
    has_batchim = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    return f"{word}{'이랑' if has_batchim else '랑'}"


def _profile(category: str | None) -> dict | None:
    if not category:
        return None
    if category in CATEGORY_PROFILES:
        return CATEGORY_PROFILES[category]
    return next((p for name, p in CATEGORY_PROFILES.items() if name in category or category in name), None)


def _items_question(category: str | None) -> str:
    item = (_profile(category) or {}).get("item", "상품")
    return f"대표 {_rang(item)} 가격을 알려 주세요."


def _register_question(slot: str, draft: dict) -> str:
    return _items_question(draft.get("category")) if slot == "items" else REGISTER_QUESTIONS[slot]


def _history(iv: Interview) -> list[dict]:
    return [{"q": t["question"], "a": t["transcript"]} for t in iv.turns[-HISTORY:]]


def _progress(iv: Interview) -> int:
    if iv.status != "active":
        return 100
    if iv.kind == "register":
        done = len(REQUIRED) - len(_missing(iv.state["draft"]))
        return min(95, int(done / len(REQUIRED) * 90))
    return min(95, int(len(iv.state.get("covered", [])) / len(STORY_BEATS) * 90))


def _out(iv: Interview, llm: LLMProvider, say: str, transcript: str | None = None, heard: bool = True) -> dict:
    if heard:  # 다시 묻는 말은 기록하지 않는다 (다음 답의 질문은 원래 질문)
        iv.state = {**iv.state, "last_say": say}
    return {
        "interview_id": iv.id,
        "kind": iv.kind,
        "turn": len(iv.turns),
        "transcript": transcript,
        "heard": heard,
        "say_ko": say,
        "audio_url": tts.audio_url(llm, say),
        "done": iv.status != "active",
        "progress": _progress(iv),
        "draft": iv.state.get("draft") if iv.kind == "register" else None,
        "covered": iv.state.get("covered") if iv.kind == "story" else None,
        "mode": llm.name,
    }


def detail(iv: Interview, llm: LLMProvider) -> dict:
    return {
        "interview_id": iv.id,
        "kind": iv.kind,
        "status": iv.status,
        "store_id": iv.store_id,
        "turns": [{"question": t["question"], "transcript": t["transcript"]} for t in iv.turns],
        "draft": iv.state.get("draft"),
        "needs_check": needs_check(iv),
        "session_id": iv.result.get("session_id"),
        "covered": iv.state.get("covered"),
        "quotes": iv.result.get("quotes"),
        "script_ko": iv.result.get("script_ko"),
        "mode": llm.name,
    }


def start(db: Session, llm: LLMProvider, kind: str, store_id: int | None, market_id: int | None) -> dict:
    store = None
    if store_id is not None:
        store = db.get(Store, store_id)
        if not store:
            raise not_found("가게")
        market_id = store.market_id
    elif kind == "story":
        raise AppError("VALIDATION_ERROR", "이야기 인터뷰는 store_id 가 필요해요. 가게를 먼저 등록해 주세요.", 422)
    if market_id is not None and not db.get(Market, market_id):
        raise not_found("시장")
    if market_id is None:
        first = db.query(Market).order_by(Market.id).first()
        if not first:
            raise AppError("NO_MARKET", "시장 데이터가 없습니다. 시드를 먼저 실행하세요.", 409)
        market_id = first.id

    if kind == "register":
        state = {
            "draft": {"store_name": None, "category": None, "items": [], "payment": [], "hours": None, "extras": {}},
            "needs_check": [],
            "asking": "store_name",
            "extra_asked": 0,
        }
        say = REGISTER_QUESTIONS["store_name"]
    else:
        state = {"covered": [], "anecdote": False, "asking": "origin"}
        say = f"{store.name} 사장님, 이야기 좀 들려주세요. {STORY_QUESTIONS['origin']}"
    iv = Interview(kind=kind, store_id=store_id, market_id=market_id, turns=[], state=state, result={})
    db.add(iv)
    db.flush()
    out = _out(iv, llm, say)
    out["fillers"] = [{"text": t, "audio_url": tts.audio_url(llm, t)} for t in FILLERS]
    db.commit()
    return out


def _compact(text: str) -> str:
    return re.sub(r"[\s.,!?~·'\"]+", "", text or "")


def _is_echo(transcript: str, last_say: str) -> bool:
    """방금 답이 AI 가 한 말을 길게 그대로 따라 한 것인지. 질문 낱말 몇 개를 쓴 짧은 답("건어물 가게예요")은 아니다."""
    a, b = _compact(transcript), _compact(last_say)
    if len(a) < 8 or not b:
        return False
    m = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size >= 0.7 * len(a)


def _save_audio(iv: Interview, data: bytes) -> str:
    path = get_settings().audio_path / "interviews" / str(iv.id) / f"{len(iv.turns)}-{int(time.time() * 1000)}.{ai.audio_ext(data)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


async def turn(
    db: Session, llm: LLMProvider, iv: Interview, audio: bytes | None, filename: str, text: str | None, turn_no: int | None
) -> dict:
    if iv.status != "active":
        raise AppError("INTERVIEW_DONE", "인터뷰가 끝났어요.", 409)
    if turn_no is not None and turn_no != len(iv.turns):
        raise AppError("TURN_MISMATCH", f"지금은 {len(iv.turns)}번째 답을 기다리고 있어요.", 409)
    asking = iv.state.get("asking", "")
    audio_path = None
    typed = " ".join((text or "").split())
    if audio:
        audio_path = _save_audio(iv, audio)
        try:
            if ai.is_mock(llm):
                transcript = MOCK_ANSWERS.get(asking, "네")
            else:
                transcript = await asyncio.wait_for(ai.transcribe(llm, audio, filename, "story"), STT_BUDGET)
        except TimeoutError:
            logger.warning("인터뷰 음성 인식 시간 초과")
            transcript = ""
        except AppError as e:
            if e.code != "STT_FAILED":
                raise
            transcript = ""
        # 서버 음성 인식이 실패하면 브라우저가 실시간으로 받아 적은 글(text)을 쓴다
        transcript = transcript or ai.drop_stt_hallucinations(typed)
    else:
        transcript = typed
    if _is_echo(transcript, iv.state.get("last_say", "")):
        transcript = ""  # 스피커로 나간 AI 질문이 마이크로 다시 들어온 것
    if not transcript:
        # 통화가 끊기지 않게 에러 대신 다시 묻는다
        misses = iv.state.get("misses", 0) + 1
        iv.state = {**iv.state, "misses": misses}
        out = _out(iv, llm, f"{REPROMPT_AGAIN if misses >= 2 else REPROMPT} {iv.state.get('last_say', '')}".strip(), heard=False)
        db.commit()
        return out
    iv.state = {**iv.state, "misses": 0}

    # alt: 브라우저가 받아 적은 글. 서버 인식과 다르면 LLM 이 둘을 보고 맞는 쪽을 고른다 ("분식집" / "한식집")
    alt = ai.drop_stt_hallucinations(typed) if audio and typed and _compact(typed) != _compact(transcript) else None
    iv.turns = [*iv.turns, {"question": iv.state.get("last_say", ""), "transcript": transcript, "audio_path": audio_path, "alt": alt}]
    if _STOP_RE.search(transcript):
        say, finish = "", True
    elif iv.kind == "register":
        say, finish = await _register_step(llm, iv, transcript)
    else:
        say, finish = await _story_step(db, llm, iv, transcript)
    if finish or len(iv.turns) >= MAX_TURNS[iv.kind]:
        _finish(db, iv)
        say = CLOSING[iv.kind]
    out = _out(iv, llm, say, transcript)
    db.commit()
    return out


def finish(db: Session, llm: LLMProvider, iv: Interview) -> dict:
    """사장님이 '그만'을 눌렀을 때."""
    if iv.status == "active":
        _finish(db, iv)
    out = _out(iv, llm, CLOSING[iv.kind])
    db.commit()
    return out


def _finish(db: Session, iv: Interview) -> None:
    iv.status = "done"
    if iv.kind == "register":
        sync_session(db, iv)


# ---------- register ----------


def _missing(draft: dict) -> list[str]:
    return [k for k in REQUIRED if not draft.get(k)]


def _match_category(text: str) -> str:
    for name, p in CATEGORY_PROFILES.items():
        if any(k in f"{text} " for k in p["keywords"]):
            return name
    return re.sub(r"\s*(가게|집)?\s*(이에요|예요|에요|입니다|이요|요)?[.!~\s]*$", "", text).strip()[:30]


def _clean_item(m: dict) -> dict | None:
    items = ai.normalize("menus", {"menus": [m]})["menus"]
    if not items:
        return None
    unit = m.get("unit")
    return {**items[0], "unit": str(unit).strip()[:30] if unit else None}


def _clean_draft(raw, old: dict) -> dict:
    """LLM 출력이나 사장님 수정값을 draft 형태로 맞춘다. 형식이 틀리면 ValueError/TypeError."""
    if not isinstance(raw, dict):
        raise ValueError("draft 아님")
    raw = {**old, **raw}

    def text(key: str, limit: int) -> str | None:
        v = raw.get(key)
        return str(v).strip()[:limit] or None if v else None

    extras = raw.get("extras") or {}
    if not isinstance(extras, dict):
        raise ValueError("extras 아님")
    return {
        "store_name": text("store_name", 100),
        "category": text("category", 30),
        "items": [x for m in list(raw.get("items") or [])[:30] if isinstance(m, dict) and (x := _clean_item(m))],
        "payment": [p for p in PAYMENTS if p in (raw.get("payment") or [])],
        "hours": text("hours", 200),
        "extras": {str(k).strip()[:20]: str(v).strip()[:100] for k, v in list(extras.items())[:10] if k and v},
    }


def _merge_items(old: list[dict], new: list, remove=None) -> list:
    """LLM 이 보낸 메뉴를 이름으로 기존 목록에 합친다. 빠뜨린 기존 메뉴가 사라지거나 가격이 지워지지 않게.
    remove 에 있는 이름만 뺀다 ("순대는 이제 안 팔아요")."""
    gone = {_compact(str(x)) for x in remove or []}
    merged = {_compact(m["name_ko"]): dict(m) for m in old}
    for m in new:
        if not isinstance(m, dict) or not m.get("name_ko"):
            continue
        key = _compact(str(m["name_ko"]))
        before = merged.get(key, {})
        merged[key] = {**before, **{k: v for k, v in m.items() if v is not None or k not in before}}
    return [m for k, m in merged.items() if k not in gone]


def _rule_update(draft: dict, asking: str, transcript: str) -> dict:
    """LLM 없이 지금 물은 항목만 규칙으로 채운다."""
    d = {**draft}
    if asking == "store_name":
        d["store_name"] = ai._RULES["store_name"](transcript)[0]["name_ko"] or None
    elif asking == "category":
        d["category"] = _match_category(transcript) or None
    elif asking == "items":
        items = [{**m, "unit": None} for m in ai._RULES["menus"](transcript)[0]["menus"]]
        d["items"] = _merge_items(d["items"], items)
    elif asking == "payment":
        d["payment"] = ai._RULES["payment"](transcript)[0]["payment"]
    elif asking == "hours":
        d["hours"] = ai._RULES["hours"](transcript)[0]["hours"] or transcript[:200]
    return d


def _near(text: str, corpus: str, ratio: float = 0.6) -> bool:
    """text 가 사장님이 한 말(corpus, 띄어쓰기 뺀 글)에 있거나 비슷한 부분이 있는지. 음성 인식 오타("떡뽁이")는 허용한다."""
    c = _compact(text)
    if not c or c in corpus:
        return bool(c)
    n = len(c)
    return any(difflib.SequenceMatcher(None, c, corpus[i : i + n]).ratio() >= ratio for i in range(max(len(corpus) - n + 1, 1)))


_NUMBER_WORDS = re.compile(r"\d|[일이삼사오육칠팔구십백천만]\s*(원|천|만|백)|원|천|만")
_TIME_WORDS = re.compile(r"\d|시|반|요일|휴무|쉬|매일|아침|저녁|점심|새벽|밤|오전|오후|주말|평일|연중|무휴|열|아홉|여덟|일곱|여섯|다섯")


def _ground(new: dict, old: dict, iv: Interview, transcript: str, needs: list[str]) -> dict:
    """LLM 이 사장님이 하지 않은 말을 채운 경우(환각)를 걸러 낸다. 의심스러운 건 지우거나 확인 목록에 올린다."""
    said = " ".join(f"{t['transcript']} {t.get('alt') or ''}" for t in iv.turns)  # 이번 답 포함, 브라우저 인식도
    corpus = _compact(said)
    if new["store_name"] != old.get("store_name") and new["store_name"] and not _near(new["store_name"], corpus):
        needs.append("가게 이름 (말씀과 달라요)")
        new["store_name"] = old.get("store_name") or ai._RULES["store_name"](transcript)[0]["name_ko"] or None
    old_items = {_compact(m["name_ko"]): m for m in old.get("items") or []}
    items = []
    for m in new["items"]:
        key, before = _compact(m["name_ko"]), old_items.get(_compact(m["name_ko"]))
        if not before and key not in corpus:
            if not _near(m["name_ko"], corpus):
                logger.info("인터뷰: 사장님이 말하지 않은 상품을 뺍니다")
                continue
            needs.append(f"{m['name_ko']} 이름 확인")
        if m["price"] is not None and (not before or before["price"] != m["price"]) and not _NUMBER_WORDS.search(transcript):
            m["price"] = before["price"] if before else None  # 이번 답에 숫자가 없는데 가격이 생겼다
        items.append(m)
    new["items"] = items
    yes, _ = ai._payment_sets(said)
    for code in [p for p in new["payment"] if p not in (old.get("payment") or []) and p not in yes]:
        new["payment"].remove(code)
        needs.append(f"{PAYMENT_KO[code]} 가능 여부")
    if new["hours"] != old.get("hours") and new["hours"] and not _TIME_WORDS.search(transcript):
        new["hours"] = old.get("hours")
        needs.append("영업시간")
    extras = dict(old.get("extras") or {})
    for k, v in new["extras"].items():
        if extras.get(k) == v or any(_compact(w) in corpus for w in re.split(r"[\s,·/]+", v) if len(_compact(w)) >= 2):
            extras[k] = v
    new["extras"] = {k: v for k, v in extras.items() if k in new["extras"]}
    return new


def _numbers_ok(say: str, *sources: str) -> bool:
    """AI 가 할 말에 나온 숫자가 사장님 말이나 정리한 내용에 있는지 (지어낸 연도·가격 막기)."""
    known = re.sub(r"[,\s]", "", " ".join(sources))
    return all(n in known for n in re.findall(r"\d+", say.replace(",", "")))


def _echo(asking: str, draft: dict) -> str:
    if asking == "store_name" and draft.get("store_name"):
        return f"{draft['store_name']},"
    if asking == "category" and draft.get("category"):
        return f"{draft['category']} 가게군요."
    return ""  # 맞장구는 프론트가 이미 틀었다


async def _register_step(llm: LLMProvider, iv: Interview, transcript: str) -> tuple[str, bool]:
    st = iv.state
    draft, asking, extra_asked = st["draft"], st.get("asking", ""), st.get("extra_asked", 0)
    needs = list(st.get("needs_check", []))
    say, next_asking, llm_finish, used_llm = None, None, False, False
    if not ai.is_mock(llm):
        payload = {
            "draft": draft,
            "needs_check": needs,
            "asking": asking,
            "history": _history(iv)[:-1],
            "answer": transcript,
            "answer_alt": iv.turns[-1].get("alt"),
            "extra_asked": extra_asked,
        }
        try:
            data = await asyncio.wait_for(
                ai.complete_json(llm, REGISTER_INTERVIEW_PROMPT, json.dumps(payload, ensure_ascii=False), TURN_MAX_TOKENS),
                TURN_BUDGET,
            )
            updates = dict(data.get("updates") or {})
            if isinstance(updates.get("items"), list) or data.get("remove_items"):
                updates["items"] = _merge_items(draft["items"], updates.get("items") or [], data.get("remove_items"))
            new = _clean_draft(updates, draft)
            needs = [str(x)[:100] for x in (data.get("needs_check") or [])][:15]  # LLM 이 지금 남은 것만 다시 보낸다
            say = str(data.get("say") or "").strip()[:200] or None
            next_asking = str(data.get("asking") or "")
            llm_finish = bool(data.get("finish"))
            used_llm = True
        except (LLMError, ValueError, TypeError, AttributeError, TimeoutError) as e:
            logger.warning("인터뷰 LLM 실패, 고정 질문으로 이어 갑니다: %s", type(e).__name__)
    if not used_llm:
        new = _rule_update(draft, asking, transcript)
    else:
        new = _ground(new, draft, iv, transcript, needs)
        if say and not _numbers_ok(say, " ".join(t["transcript"] for t in iv.turns), json.dumps(new, ensure_ascii=False)):
            say = None  # 지어낸 숫자가 들어간 말은 버리고 아래 고정 질문으로

    # LLM 이 숫자를 틀리거나 "카드는 안 돼요"를 가능으로 읽는 경우를 막는다 (기존 등록과 같은 검사)
    ai._cross_check_menus(new["items"], transcript, needs)
    _, no = ai._payment_sets(transcript)
    for code in [p for p in new["payment"] if p in no]:
        new["payment"].remove(code)
        needs.append(f"{PAYMENT_KO[code]} 가능 여부")
    for m in new["items"]:
        if m["price"] is None and not any(m["name_ko"] in n for n in needs):
            needs.append(f"{m['name_ko']} 가격")

    asked = dict(st.get("asked") or {asking: 1})  # 항목마다 물어본 횟수
    tired = lambda k: asked.get(k, 0) >= MAX_ASK_PER_FIELD  # noqa: E731  두 번 물었으면 확인 목록으로 넘긴다
    missing = [k for k in _missing(new) if not tired(k)]
    no_price = [m["name_ko"] for m in new["items"] if m["price"] is None]
    finish = False
    if next_asking in REQUIRED and say and (tired(next_asking) or (next_asking != "items" and next_asking not in _missing(new))):
        say = None  # 이미 들은 걸 다시 묻거나 같은 걸 세 번째로 묻지 않는다
    if say and _CONFIRM_RE.search(say):
        say = None  # "맞나요?" 같은 확인은 요약 화면에서 한다
    if no_price and not tired("items") and next_asking != "items" and (asking == "items" or not missing):
        # 가격 빠진 메뉴는 메뉴 답 바로 다음(또는 필수 항목을 다 들은 뒤)에 한 번에 묶어서 한 번만 묻는다
        next_asking = "items"
        say = f"{', '.join(no_price[:3])} 가격은 얼마예요?"
    elif missing:
        # 필수 항목이 비어 있으면 LLM 이 끝내자고 하거나 업종별 질문으로 넘어가도 필수 항목을 묻는다.
        if not say or llm_finish or next_asking == "extra" or next_asking not in REQUIRED:
            next_asking = missing[0]
            say = f"{_echo(asking, new)} {_register_question(next_asking, new)}".strip()
    elif used_llm and not llm_finish and extra_asked < MAX_EXTRA and say and next_asking == "extra":
        extra_asked += 1
    else:
        finish = True
    if not finish and next_asking:
        asked[next_asking] = asked.get(next_asking, 0) + 1
    state = {"draft": new, "needs_check": list(dict.fromkeys(needs)), "asking": next_asking, "extra_asked": extra_asked, "asked": asked}
    iv.state = {**st, **state}
    return _LEAD_FILLER.sub("", say or ""), finish


def sync_session(db: Session, iv: Interview) -> OnboardingSession:
    """인터뷰 결과를 끝난 등록 세션으로 옮긴다. 요약·미리보기·올리기는 기존 등록 API 를 그대로 쓴다."""
    d = iv.state["draft"]
    store = db.get(Store, iv.store_id) if iv.store_id else None
    needs = needs_check(iv)

    def entry(parsed: dict, needs_check: list[str] | None = None) -> dict:
        return {"transcript": "", "parsed": parsed, "needs_check": needs_check or [], "audio_path": None, "confirmed": True}

    answers = {
        "store_name": entry({"name_ko": d.get("store_name") or ""}, needs),
        "menus": entry({"menus": [{k: m[k] for k in ("name_ko", "price", "spicy_level", "is_ai_guess")} for m in d["items"]]}),
        "payment": entry({"payment": d["payment"]}),
        "hours": entry({"hours": d.get("hours") or ""}),
        "story": entry({"story_ko": store.story_ko if store else ""}),
    }
    s = db.get(OnboardingSession, iv.result["session_id"]) if iv.result.get("session_id") else None
    if s is None:
        s = OnboardingSession(store_id=iv.store_id, market_id=iv.market_id, step=onboarding.DONE)
        db.add(s)
    s.answers = answers
    db.flush()
    iv.result = {**iv.result, "session_id": s.id}
    return s


_LABELS = {"store_name": "가게 이름", "category": "업종", "items": "상품·가격", "payment": "결제수단", "hours": "영업시간"}


def needs_check(iv: Interview) -> list[str]:
    needs = list(iv.state.get("needs_check", []))
    if iv.kind == "register":
        needs += [f"{_LABELS[k]} (인터뷰에서 못 들었어요)" for k in _missing(iv.state["draft"])]
    return needs


def edit_draft(db: Session, iv: Interview, edits: dict) -> None:
    if iv.kind != "register" or iv.status != "done":
        raise AppError("VALIDATION_ERROR", "끝난 가게 등록 인터뷰만 고칠 수 있어요.", 409)
    try:
        draft = _clean_draft(edits, iv.state["draft"])
    except (ValueError, TypeError, AttributeError) as e:
        raise AppError("VALIDATION_ERROR", "수정한 값의 형식이 올바르지 않아요.", 422) from e
    iv.state = {**iv.state, "draft": draft, "needs_check": []}  # 사장님이 직접 고친 값은 확정으로 본다
    sync_session(db, iv)
    db.commit()


async def publish(db: Session, llm: LLMProvider, iv: Interview) -> dict:
    if iv.kind != "register":
        raise AppError("VALIDATION_ERROR", "가게 등록 인터뷰만 올릴 수 있어요.", 409)
    if iv.status == "active":
        raise AppError("ONBOARDING_INCOMPLETE", "인터뷰가 아직 끝나지 않았어요.", 409)
    s = db.get(OnboardingSession, iv.result["session_id"])
    out = await onboarding.publish(db, llm, s)
    store = db.get(Store, out["store_id"])
    d = iv.state["draft"]
    store.category, store.extras = d.get("category"), d.get("extras") or None
    for menu, item in zip(store.menus, d["items"]):
        menu.unit = item.get("unit")
    iv.status, iv.store_id = "published", store.id
    db.commit()
    return out


# ---------- story ----------


def _store_facts(db: Session, iv: Interview) -> dict:
    store = db.get(Store, iv.store_id)
    return {
        "name": store.name,
        "category": store.category,
        "market": store.market.name,
        "market_address": store.market.address,
        "items": [m.name_ko for m in store.menus][:5],
        "intro": store.story_ko[:200],
    }


def _next_beat(covered: list[str], asked: dict) -> str | None:
    return next((b for b in STORY_BEATS if b not in covered and asked.get(b, 0) < MAX_ASK_PER_BEAT), None)


async def _story_step(db: Session, llm: LLMProvider, iv: Interview, transcript: str) -> tuple[str, bool]:
    st = iv.state
    covered, asking, anecdote = list(st.get("covered", [])), st.get("asking", ""), st.get("anecdote", False)
    asked = dict(st.get("asked") or {asking: 1})  # 축마다 물어본 횟수. 같은 축은 두 번까지만
    say, llm_finish, used_llm = None, False, False
    if not ai.is_mock(llm):
        payload = {
            "store": _store_facts(db, iv),
            "covered": covered,
            "asking": asking,
            "history": _history(iv)[:-1],
            "answer": transcript,
            "answer_alt": iv.turns[-1].get("alt"),
            "turn": len(iv.turns),
        }
        try:
            data = await asyncio.wait_for(
                ai.complete_json(llm, STORY_INTERVIEW_PROMPT, json.dumps(payload, ensure_ascii=False), TURN_MAX_TOKENS),
                TURN_BUDGET,
            )
            covered = list(dict.fromkeys([*covered, *(b for b in data.get("covered") or [] if b in STORY_BEATS)]))
            anecdote = anecdote or bool(data.get("anecdote"))
            say = str(data.get("say") or "").strip()[:200] or None
            asking = str(data.get("asking") or "")
            llm_finish = bool(data.get("finish"))
            used_llm = True
            if say and not _numbers_ok(say, " ".join(t["transcript"] for t in iv.turns), json.dumps(payload["store"], ensure_ascii=False)):
                say = None
        except (LLMError, ValueError, TypeError, AttributeError, TimeoutError) as e:
            logger.warning("이야기 인터뷰 LLM 실패, 고정 질문으로 이어 갑니다: %s", type(e).__name__)
    if not used_llm:
        if asking in STORY_BEATS and len(transcript) >= 10 and asking not in covered:
            covered.append(asking)
        anecdote = anecdote or len(transcript) >= 30

    # 사장님 이야기(origin)와 시장·동네 이야기(years)는 꼭 듣고, 한 축 더 + 구체적인 일화 하나
    # (두 번 물어도 안 나오면 억지로 붙잡지 않는다)
    must = all(b in covered or asked.get(b, 0) >= MAX_ASK_PER_BEAT for b in ("origin", "years"))
    enough = len(iv.turns) >= MIN_STORY_TURNS and must and len(covered) >= 4 and anecdote
    nxt = _next_beat(covered, asked)
    repeat = asking in STORY_BEATS and asked.get(asking, 0) >= MAX_ASK_PER_BEAT
    finish = (llm_finish and enough) or nxt is None or (repeat and enough)
    if not finish and (not say or llm_finish or repeat):
        asking = nxt
        say = STORY_QUESTIONS[nxt]
    if not finish and asking in STORY_BEATS:
        asked[asking] = asked.get(asking, 0) + 1
    iv.state = {**st, "covered": covered, "anecdote": anecdote, "asking": asking, "asked": asked}
    return _LEAD_FILLER.sub("", say or ""), finish


def _exact_span(transcript: str, quote: str) -> str | None:
    """quote 가 transcript 에 그대로 있으면 원문 구간을 돌려준다 (띄어쓰기·끝 문장부호 차이는 허용)."""
    words = quote.strip(" .,!?~\"'").split()
    if not words:
        return None
    m = re.search(r"\s+".join(map(re.escape, words)), transcript)
    return m.group(0) if m else None


_FILLER_ONLY = re.compile(r"^(네|예|응|음+|어+|아+|그렇죠|맞아요|글쎄요|몰라요|없어요)[.!?~\s]*$")


def _head(text: str, limit: int) -> str:
    """앞에서부터 limit 글자 안쪽, 낱말 경계에서 자른 원문 구간."""
    return text if len(text) <= limit else (text[:limit].rsplit(" ", 1)[0] or text[:limit])


def _rule_quotes(turns: list[dict]) -> list[dict]:
    """LLM 없이: 답마다 첫 문장을 골라 30초 분량까지. 이야기가 짧으면 짧은 답이라도 쓴다."""
    quotes, total = [], 0
    for i, t in enumerate(turns):
        sentence = next((s for s in re.split(r"(?<=[.!?。])\s+", t["transcript"]) if len(s) >= 8), "")
        sentence = _head(sentence, QUOTE_CHARS[1] - total)
        if len(sentence) < 8:
            continue
        quotes.append({"turn": i, "text": sentence})
        total += len(sentence)
    if total >= QUOTE_CHARS[0]:
        return quotes
    # 이야기가 거의 없을 때: 맞장구만 한 답을 빼고 짧은 답도 그대로 이어 붙인다
    used = {q["turn"] for q in quotes}
    for i, t in enumerate(turns):
        said = t["transcript"].strip()
        if i in used or len(said) < 2 or _FILLER_ONLY.match(said):
            continue
        said = _head(said, QUOTE_CHARS[1] - total)
        if said:
            quotes.append({"turn": i, "text": said})
            total += len(said)
    return sorted(quotes, key=lambda q: q["turn"])


async def compose(db: Session, llm: LLMProvider, iv: Interview) -> dict:
    """원음에서 그대로 쓸 구간을 고른다. 이미 골랐으면 그대로 돌려준다."""
    if iv.result.get("quotes") is not None:
        return iv.result
    quotes = []
    if not ai.is_mock(llm) and iv.turns:
        payload = {
            "store": _store_facts(db, iv),
            "turns": [{"turn": i, "text": t["transcript"]} for i, t in enumerate(iv.turns)],
        }
        try:
            data = await asyncio.wait_for(
                ai.complete_json(llm, STORY_COMPOSE_PROMPT, json.dumps(payload, ensure_ascii=False), TURN_MAX_TOKENS),
                COMPOSE_BUDGET,
            )
            total = 0
            for q in data.get("quotes") or []:
                i = int(q.get("turn", -1))
                span = _exact_span(iv.turns[i]["transcript"], str(q.get("text") or "")) if 0 <= i < len(iv.turns) else None
                if span and total + len(span) <= QUOTE_CHARS[1]:
                    quotes.append({"turn": i, "text": span})
                    total += len(span)
            if total < QUOTE_CHARS[0]:
                quotes = []
        except (LLMError, ValueError, TypeError, AttributeError, TimeoutError) as e:
            logger.warning("이야기 구간 고르기 실패, 규칙으로 대체: %s", type(e).__name__)
            quotes = []
    quotes = quotes or _rule_quotes(iv.turns)
    script = " ".join(q["text"] if q["text"][-1] in ".!?~" else f"{q['text']}." for q in quotes)
    iv.result = {**iv.result, "quotes": quotes, "script_ko": script}
    store = db.get(Store, iv.store_id)
    if script and store:
        store.story_ko = script  # 가게 페이지 '사장님 이야기'도 사장님 말 그대로
    db.commit()
    return iv.result


async def compose_in_background(interview_id: int, llm: LLMProvider) -> None:
    """이야기 인터뷰가 끝나면 구간을 고르고 가게 소개 번역을 다시 만든다."""
    try:
        with SessionLocal() as db:
            iv = db.get(Interview, interview_id)
            if not iv or iv.kind != "story":
                return
            await compose(db, llm, iv)
            store = db.get(Store, iv.store_id)
            if store and store.status == "published":
                await onboarding.retranslate(db, llm, store)
    except Exception as e:  # 영상 만들 때 다시 시도한다
        logger.warning("이야기 정리 실패 interview=%s: %s", interview_id, type(e).__name__)


def video_quotes(iv: Interview) -> list[dict]:
    """영상 작업에 넘길 구간: 글, 원음 경로, 그 답 전체 글(음성 맞추기용)."""
    return [
        {"text": q["text"], "audio_path": iv.turns[q["turn"]]["audio_path"], "transcript": iv.turns[q["turn"]]["transcript"]}
        for q in iv.result.get("quotes") or []
    ]
