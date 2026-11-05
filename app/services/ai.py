"""STT·LLM 정리·번역·자막. LLM 프로바이더가 mock 이면 전부 고정 응답/규칙 기반으로 동작한다.

외부 호출이 실패해도 데모가 멈추지 않도록 각 함수는 대체 결과를 돌려준다(STT 만 예외).
"""

import asyncio
import json
import logging
import math
import re

from app.core.errors import AppError
from app.llm import ChatMessage, LLMError, LLMProvider
from app.prompts import (
    TRANSLATE_LINES_PROMPT,
    TRANSLATE_PROMPT,
    VIDEO_SCRIPT_PROMPT,
    structure_prompt,
)
from app.services.i18n import GLOSSARY, PAYMENT_KO, PAYMENTS, glossary_entry, glossary_translate

logger = logging.getLogger(__name__)

STEPS = ["store_name", "menus", "payment", "hours", "story"]

QUESTIONS = {
    "store_name": ("가게 이름이 뭐예요?", "예: 영천 할매 떡볶이예요"),
    "menus": ("어떤 메뉴를 얼마에 파세요?", "예: 기름떡볶이 4천 원, 순대 5천 원이요"),
    "payment": ("어떻게 결제할 수 있어요? 현금, 카드, 해외카드, 온누리상품권 중에 말씀해 주세요.", "예: 현금이랑 카드 다 돼요"),
    "hours": ("몇 시부터 몇 시까지 하세요? 쉬는 날도 알려 주세요.", "예: 아침 10시부터 밤 9시까지, 월요일은 쉬어요"),
    "story": ("손님들에게 가게를 어떻게 소개하고 싶으세요?", "예: 40년 동안 기름떡볶이만 만들어 왔어요"),
}

# mock 모드에서 음성 대신 쓰는 사장님 답변
MOCK_TRANSCRIPTS = {
    "store_name": "영천 할매 떡볶이입니다",
    "menus": "기름떡볶이 4000원, 간장떡볶이 4000원, 순대 5000원이요",
    "payment": "현금이랑 카드 다 돼요. 온누리상품권도 받아요",
    "hours": "아침 10시부터 저녁 9시까지 하고 월요일은 쉬어요",
    "story": "40년 동안 영천시장에서 기름떡볶이만 만들어 왔어요. 외국 손님들도 맛있다고 많이 찾아와요",
}


def is_mock(llm: LLMProvider) -> bool:
    return llm.name == "mock"


# ---------- STT ----------


def audio_ext(data: bytes) -> str:
    """파일 내용으로 녹음 형식을 알아낸다. 브라우저는 파일 이름을 확장자 없는 'blob' 으로 보내는 경우가 많고,
    OpenAI 는 확장자로 형식을 판단한다. Chrome 은 webm, Safari(iOS) 는 mp4 로 녹음한다."""
    if data[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    if data[4:8] == b"ftyp":
        return "m4a"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"RIFF":
        return "wav"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    return "webm"


# 조용하거나 시끄러운 녹음에서 음성 인식 모델이 지어내는 말 (방송 자막 학습 탓). 이것만 들리면 못 들은 것으로 본다.
_STT_HALLUCINATIONS = re.compile(
    r"시청(해\s*주셔서|해주셔서)\s*감사합니다|구독(과|이랑)?\s*좋아요|좋아요(와|랑)?\s*구독|다음\s*영상에서\s*(만나요|뵙겠습니다)"
    r"|자막\s*(제공|by)|MBC\s*뉴스|KBS\s*뉴스|SBS\s*뉴스|[가-힣]+\s*뉴스\s*[가-힣]+입니다|^\W*(음+|어+|아+)?\W*$"
)


def drop_stt_hallucinations(text: str) -> str:
    return "" if _STT_HALLUCINATIONS.search(text) and len(text) < 40 else text


async def transcribe(llm: LLMProvider, audio: bytes, filename: str, step: str, hint: str | None = None) -> str:
    """hint: 가게 이름·메뉴 같은 낱말 (고유명사 인식을 돕는다)."""
    if is_mock(llm):
        return MOCK_TRANSCRIPTS[step]
    fn = getattr(llm, "transcribe", None)
    if fn is None:
        raise AppError("STT_FAILED", "지금 설정에서는 음성 인식을 쓸 수 없어요. 글로 입력해 주세요.", 503)
    try:
        # 받은 파일 이름 대신 내용에 맞는 확장자를 붙여 보낸다.
        name = f"audio.{audio_ext(audio)}"
        text = (await (fn(audio, name, prompt=hint) if hint else fn(audio, name))).strip()
    except LLMError as e:
        raise AppError("STT_FAILED", "음성을 알아듣지 못했어요. 다시 말하거나 글로 입력해 주세요.", 502) from e
    if hint and text.strip(" .") == hint.strip(" ."):  # 소리가 없으면 힌트를 그대로 돌려줄 때가 있다
        text = ""
    text = drop_stt_hallucinations(text)
    if not text:
        raise AppError("STT_FAILED", "음성이 들리지 않았어요. 다시 말하거나 글로 입력해 주세요.", 422)
    return text


# ---------- JSON 호출 공통 ----------


def parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("JSON 없음")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON 객체가 아님")
    return data


async def complete_json(llm: LLMProvider, system: str, user: str, max_tokens: int | None = None) -> dict:
    """JSON 파싱 실패 시 1회 재시도. LLMError 는 그대로 올린다."""
    for attempt in range(2):
        text = await llm.complete([ChatMessage(role="user", content=user)], system, max_tokens=max_tokens)
        try:
            return parse_json(text)
        except ValueError:
            logger.warning("LLM JSON 파싱 실패 (%d회)", attempt + 1)
    raise ValueError("LLM JSON 파싱 실패")


# ---------- 단계별 정리 ----------

_ENDINGS = re.compile(r"\s*(이라고 합니다|라고 합니다|이라고 해요|라고 해요|이에요|예요|에요|입니다|이요|요|임)?[.!~\s]*$")
_KO_DIGIT = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_N = r"(?:\d+|[일이삼사오육칠팔구])?"
_PRICE_RE = re.compile(r"(?P<num>\d[\d,]*)\s*원|(?P<ko>(?:%s\s*만)?\s*(?:%s\s*천)?\s*(?:%s\s*백)?)\s*원" % (_N, _N, _N))
_UNIT_RE = re.compile(r"(\d+|[일이삼사오육칠팔구])?\s*(만|천|백)")
_HOUR_WORDS = {"열두": 12, "열한": 11, "열": 10, "아홉": 9, "여덟": 8, "일곱": 7, "여섯": 6, "다섯": 5, "네": 4, "세": 3, "두": 2, "한": 1}
_HOUR_RE = re.compile(
    r"(아침|오전|낮|오후|저녁|밤)?\s*(\d{1,2}|" + "|".join(_HOUR_WORDS) + r")\s*시(?:\s*(\d{1,2})\s*분|\s*(반))?"
)


def _clean(text: str) -> str:
    text = " ".join(text.split())
    m = _ENDINGS.search(text)
    cleaned = text[: m.start()] if m else text
    # "떡볶이에요"의 "이"는 조사가 아니라 이름의 일부다. "이"를 붙였을 때 음식 이름이 되면 살린다.
    if m and (m.group(1) or "").startswith("이") and any((cleaned + "이").endswith(k) for k in GLOSSARY if k.endswith("이")):
        cleaned += "이"
    return cleaned


def _price(m: re.Match) -> int | None:
    """"4,000원", "4천원", "사천 원", "만 오천 원", "1만5천원" → 정수. 숫자가 없으면 None."""
    if m.group("num"):
        return int(m.group("num").replace(",", ""))
    total = 0
    for n, unit in _UNIT_RE.findall(m.group("ko") or ""):
        count = int(n) if n.isdigit() else _KO_DIGIT.get(n, 1)
        total += count * {"만": 10000, "천": 1000, "백": 100}[unit]
    return total or None


def _rule_store_name(text: str) -> tuple[dict, list[str]]:
    # 소개하는 말이 앞에 있을 때만 지운다. "은혜분식"의 "은"처럼 이름 첫 글자를 조사로 보지 않게.
    name = re.sub(
        r"^(?:(?:저희|우리|여기)\s*(?:가게\s*)?(?:이름은|이름이|는|은)|가게\s*(?:이름은|이름이|는|은)|이름은|이름이)\s+",
        "",
        _clean(text),
    )
    return {"name_ko": name[:100]}, ([] if name else ["가게 이름"])


_SEP_RE = re.compile(r"[,，、/·]|그리고|하고|랑\s|이고\s|이며\s")
_CONNECTORS = {"", "이", "고", "요", "이요", "이고", "랑", "이랑", "그리고", "하고", "및", "또"}


def _menu_name(raw: str) -> str:
    name = re.sub(r"\s*(한|1)\s*(그릇|줄|인분|개|접시|꼬치|봉지)\s*$", "", raw.strip())
    name = re.sub(r"\s*(은|는|가|도|에)\s*$", "", name).strip()  # "이"는 떡볶이처럼 이름 끝일 수 있어 빼지 않는다
    name = re.sub(r"^(이고|이요|이랑|이며|그리고|하고|요|고)\s+", "", _clean(name))
    name = re.sub(r"\s*(도\s*)?(있어|있고|있습니다|팔아|팔고|팔아요|있어요)$", "", name).strip()
    return "" if name in _CONNECTORS or not re.search(r"[가-힣A-Za-z]", name) or len(name) > 30 else name


def _rule_menus(text: str) -> tuple[dict, list[str]]:
    """가격을 먼저 찾고, 가격 바로 앞 말을 메뉴 이름으로 본다. 가격 없는 이름은 price None."""
    menus, needs = [], []

    def add(raw: str, price: int | None) -> None:
        if not (name := _menu_name(raw)):
            return
        if price is None:
            needs.append(f"{name} 가격")
        entry = glossary_entry(name)
        menus.append({"name_ko": name, "price": price, "spicy_level": entry["spicy"] if entry else 0, "is_ai_guess": True})

    pos = 0
    for m in _PRICE_RE.finditer(text):
        if (price := _price(m)) is None:
            continue
        parts = _SEP_RE.split(text[pos : m.start()])
        for part in parts[:-1]:
            add(part, None)
        add(parts[-1], price)
        pos = m.end()
    for part in _SEP_RE.split(text[pos:]):
        add(part, None)
    if not menus:
        needs.append("메뉴와 가격")
    return {"menus": menus}, needs


# 결제수단 말 → 코드. 해외카드를 먼저 지워야 "카드"가 두 번 잡히지 않는다.
_PAY_PATTERNS = [
    ("foreign_card", r"(해외|외국)\s*카드|비자|마스터"),
    ("card", r"카드"),
    ("cash", r"현금"),
    ("onnuri", r"온누리|상품권"),
]
_NEG_RE = re.compile(r"안\s*(돼|됩|되|받|해)|안돼|못|불가|없")
# "카드는 안 돼요. 현금만 받고 온누리상품권은 안 받아요" → 절마다 따로 본다
_CLAUSE_RE = re.compile(r"[.,!?。\n]|(?<=요)\s|(?<=다)\s|는데|지만|고\s|(?:어|아|라)서\s|니까\s")


def _payment_sets(text: str) -> tuple[set[str], set[str]]:
    """(된다고 한 수단, 안 된다고 한 수단)"""
    yes: set[str] = set()
    no: set[str] = set()
    for clause in _CLAUSE_RE.split(text):
        negated = bool(_NEG_RE.search(clause))
        for code, pattern in _PAY_PATTERNS:
            if re.search(pattern, clause):
                (no if negated else yes).add(code)
                clause = re.sub(pattern, " ", clause)
    return yes, no


def _rule_payment(text: str) -> tuple[dict, list[str]]:
    yes, no = _payment_sets(text)
    needs = []
    for code in yes & no:  # 같은 수단을 된다고도, 안 된다고도 함
        needs.append(f"{PAYMENT_KO[code]} 가능 여부")
    payment = [p for p in PAYMENTS if p in yes and p not in no]
    if "foreign_card" not in yes | no:
        needs.append("해외카드 가능 여부")
    if not payment:
        needs.append("결제 방법")
    return {"payment": payment}, needs


def _rule_hours(text: str) -> tuple[dict, list[str]]:
    hours = []  # [(시, 분)]
    for mod, h, minute, half in _HOUR_RE.findall(text):
        n = int(h) if h.isdigit() else _HOUR_WORDS[h]
        if mod in ("오후", "저녁", "밤") and n < 12:
            n += 12
        hours.append((n, int(minute) if minute else 30 if half else 0))
    parts = []
    if "매일" in text or "연중무휴" in text:
        parts.append("매일")
    if len(hours) >= 2:
        (start, sm), (end, em) = hours[0], hours[1]
        if end <= start and end < 12:
            end += 12
        parts.append(f"{start:02d}:{sm:02d}-{end:02d}:{em:02d}")
    closed = re.findall(r"([월화수목금토일])요일", text)
    if closed and re.search(r"쉬|휴무|닫", text):
        parts.append(f"{'·'.join(dict.fromkeys(closed))}요일 휴무")
    if len(hours) < 2:
        return {"hours": " ".join(parts) or _clean(text)}, ["영업시간"]
    # "분"이 있는데 시간 옆에서 못 읽었으면(예: "삼십 분") 놓친 정보가 있다고 보고 확인을 요청한다
    if text.count("분") > sum(1 for _, _, minute, _ in _HOUR_RE.findall(text) if minute):
        return {"hours": ", ".join(parts)}, ["영업시간 분 단위"]
    return {"hours": ", ".join(parts)}, []


def _rule_story(text: str) -> tuple[dict, list[str]]:
    story = " ".join(text.split())
    if story and story[-1] not in ".!?":
        story += "."
    return {"story_ko": story[:1000]}, ([] if story else ["가게 이야기"])


_RULES = {
    "store_name": _rule_store_name,
    "menus": _rule_menus,
    "payment": _rule_payment,
    "hours": _rule_hours,
    "story": _rule_story,
}


def normalize(step: str, data: dict) -> dict:
    """LLM 출력과 사장님 수정값을 같은 형태로 맞춘다. 형식이 틀리면 ValueError/TypeError."""
    if step == "store_name":
        return {"name_ko": str(data.get("name_ko") or "").strip()[:100]}
    if step == "menus":
        menus = []
        for m in list(data.get("menus") or [])[:30]:
            name = str(m.get("name_ko") or "").strip()[:100]
            if not name:
                continue
            price = m.get("price")
            price = int(str(price).replace(",", "")) if price not in (None, "") else None
            spicy = m.get("spicy_level")
            if spicy is None:
                entry = glossary_entry(name)
                spicy = entry["spicy"] if entry else 0
            menus.append(
                {
                    "name_ko": name,
                    "price": max(price, 0) if price is not None else None,
                    "spicy_level": min(max(int(spicy), 0), 3),
                    "is_ai_guess": bool(m.get("is_ai_guess", False)),
                }
            )
        return {"menus": menus}
    if step == "payment":
        return {"payment": [p for p in PAYMENTS if p in (data.get("payment") or [])]}
    if step == "hours":
        return {"hours": str(data.get("hours") or "").strip()[:200]}
    if step == "story":
        return {"story_ko": str(data.get("story_ko") or "").strip()[:1000]}
    raise ValueError(step)


def _cross_check_menus(menus: list[dict], transcript: str, needs: list[str]) -> None:
    """LLM 이 숫자를 틀리는 경우가 있어("사천 원" → 3000) 규칙 기반 결과와 비교한다.
    가격이 다르면 규칙 값을 쓰고 확인 요청. AI 가 추측한 맵기는 용어집 값을 우선한다."""
    rule = {m["name_ko"].replace(" ", ""): m for m in _rule_menus(transcript)[0]["menus"]}
    for m in menus:
        r = rule.get(m["name_ko"].replace(" ", ""))
        if r and r["price"] is not None and r["price"] != m["price"]:
            needs.append(f"{m['name_ko']} 가격 ({r['price']:,}원 맞는지)")
            m["price"] = r["price"]
        entry = glossary_entry(m["name_ko"])
        if m["is_ai_guess"] and entry:
            m["spicy_level"] = entry["spicy"]


STRUCTURE_BUDGET = 8.0  # 초. 넘으면 규칙 기반 결과로 바로 확인 화면을 띄운다
STRUCTURE_MAX_TOKENS = 400  # 출력이 짧은 JSON 이라 작게 둬서 빨리 받는다


async def structure_step(llm: LLMProvider, step: str, transcript: str) -> tuple[dict, list[str]]:
    # 가게 이름·이야기는 말한 그대로 쓰면 되므로 LLM 을 부르지 않는다 (빠르고 말을 바꾸지 않음).
    if is_mock(llm) or step in ("store_name", "story"):
        return _RULES[step](transcript)
    if step == "hours":
        parsed, needs = _rule_hours(transcript)
        if not needs:  # 규칙으로 시간을 알아들었으면 끝
            return parsed, needs
    try:
        data = await asyncio.wait_for(
            complete_json(llm, structure_prompt(step), transcript, STRUCTURE_MAX_TOKENS), STRUCTURE_BUDGET
        )
        parsed = normalize(step, data)
        needs = [str(x)[:100] for x in (data.get("needs_check") or [])][:10]
    except (LLMError, ValueError, TypeError, AttributeError, TimeoutError) as e:
        logger.warning("단계 정리 실패, 규칙 기반으로 대체: %s", type(e).__name__)
        parsed, needs = _RULES[step](transcript)
        needs = ["AI 정리에 실패해서 말씀하신 그대로 정리했어요. 확인해 주세요.", *needs]
    if step == "payment":
        # LLM 이 "카드는 안 돼요"를 카드 가능으로 읽는 경우를 막는다
        _, no = _payment_sets(transcript)
        for code in [p for p in parsed["payment"] if p in no]:
            parsed["payment"].remove(code)
            needs.append(f"{PAYMENT_KO[code]} 가능 여부")
    if step == "menus":
        _cross_check_menus(parsed["menus"], transcript, needs)
        for m in parsed["menus"]:
            if m["price"] is None and not any(m["name_ko"] in n for n in needs):
                needs.append(f"{m['name_ko']} 가격")
    return parsed, needs


def confirm_text(step: str, parsed: dict) -> str:
    if step == "store_name":
        return f"가게 이름은 '{parsed['name_ko']}' 맞나요?"
    if step == "menus":
        items = [f"{m['name_ko']} {m['price']:,}원" if m["price"] is not None else f"{m['name_ko']}(가격 미정)" for m in parsed["menus"]]
        return f"메뉴는 {', '.join(items)} 맞나요?" if items else "메뉴를 알아듣지 못했어요. 다시 말씀해 주세요."
    if step == "payment":
        methods = [PAYMENT_KO[p] for p in parsed["payment"]]
        return f"결제는 {', '.join(methods)} 가능, 맞나요?" if methods else "결제 방법을 알아듣지 못했어요. 다시 말씀해 주세요."
    if step == "hours":
        return f"영업시간은 '{parsed['hours']}' 맞나요?"
    return f"가게 소개는 이렇게 보여드릴게요. \"{parsed['story_ko']}\" 괜찮으세요?"


# ---------- 번역 ----------


def fallback_translation(name_ko: str, story_ko: str, menus: list[dict], lang: str) -> dict:
    return {
        "store": {"name": name_ko, "description": story_ko, "how_to_eat": ""},
        "menus": {m["id"]: glossary_translate(m["name_ko"], lang) for m in menus},
    }


async def translate_store(
    llm: LLMProvider, name_ko: str, story_ko: str, menus: list[dict], langs: tuple[str, ...] = ("en", "zh")
) -> dict[str, dict]:
    """menus: [{id, name_ko, price, spicy_level}]
    반환: {lang: {"store": {name, description}, "menus": {id: {name, description, how_to_eat}}}}
    실패한 항목은 용어집, 그것도 없으면 한국어 원문."""
    result = {lang: fallback_translation(name_ko, story_ko, menus, lang) for lang in langs}
    if is_mock(llm):
        return result
    payload = json.dumps({"langs": list(langs), "store": {"name": name_ko, "story": story_ko}, "menus": menus}, ensure_ascii=False)
    try:
        data = await complete_json(llm, TRANSLATE_PROMPT, payload)
    except (LLMError, ValueError) as e:
        logger.warning("번역 실패, 원문/용어집으로 대체: %s", type(e).__name__)
        return result
    for lang in langs:
        d = data.get(lang)
        if not isinstance(d, dict):
            continue
        store = d.get("store") or {}
        if isinstance(store, dict):
            for field in ("name", "description"):
                if isinstance(store.get(field), str) and store[field].strip():
                    result[lang]["store"][field] = store[field].strip()
        menus_out = d.get("menus")
        if not isinstance(menus_out, list):
            continue  # 형식이 틀리면 용어집·원문 결과를 그대로 쓴다
        for m in menus_out:
            if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"] in result[lang]["menus"]:
                target = result[lang]["menus"][m["id"]]
                for field in ("name", "description", "how_to_eat"):
                    if isinstance(m.get(field), str) and m[field].strip():
                        target[field] = m[field].strip()
    return result


# ---------- 영상 자막 ----------


def _first_sentence(text: str, limit: int) -> str:
    s = re.split(r"(?<=[.!?。！？])\s*", text.strip())[0] if text else ""
    return s if len(s) <= limit else s[: limit - 1] + "…"


def fallback_script(ctx: dict, langs: list[str]) -> dict[str, list[str]]:
    """ctx: store_name, market_name, menu_name, price, story, 그리고 언어별 i18n: {lang: {store_name, market_name, menu_name, story}}"""
    price = ctx.get("price")
    tails = {"ko": "어서 오세요!", "en": "Come visit us!", "zh": "欢迎光临！"}
    script = {}
    for lang in ["ko", *[x for x in langs if x != "ko"]]:
        t = ctx["i18n"].get(lang, {}) if lang != "ko" else ctx
        lines = [f"{t.get('store_name') or ctx['store_name']} · {t.get('market_name') or ctx['market_name']}"]
        if ctx.get("menu_name"):
            menu = t.get("menu_name") or ctx["menu_name"]
            lines.append(f"{menu} {price:,}원" if lang == "ko" and price else f"{menu} ₩{price:,}" if price else menu)
        story = t.get("story") if lang != "ko" else ctx.get("story")
        if story and (lang == "ko" or story != ctx.get("story")):
            lines.append(_first_sentence(story, 40 if lang == "en" else 24))
        lines.append(tails.get(lang, tails["en"]))
        script[lang] = lines
    return script


async def video_script(llm: LLMProvider, ctx: dict, langs: list[str]) -> dict[str, list[str]]:
    script = fallback_script(ctx, langs)
    if is_mock(llm):
        return script
    facts = {k: ctx.get(k) for k in ("store_name", "market_name", "menu_name", "price", "story", "transcript")}
    payload = json.dumps({"langs": ["ko", *langs], "facts": facts}, ensure_ascii=False)
    try:
        data = await complete_json(llm, VIDEO_SCRIPT_PROMPT, payload)
    except (LLMError, ValueError) as e:
        logger.warning("자막 생성 실패, 템플릿으로 대체: %s", type(e).__name__)
        return script
    for lang in script:
        lines = data.get(lang)
        if isinstance(lines, list) and 3 <= len(lines) <= 6 and all(isinstance(x, str) and x.strip() for x in lines):
            script[lang] = [x.strip()[:60] for x in lines]
    return script


# ---------- 사장님 스크립트 → 자막 ----------

MAX_SCRIPT_LINES = 7  # 6줄까지 줄당 5초, 7줄은 줄당 4초(28초). 넘으면 SCRIPT_TOO_LONG
_LINE_CHARS = 25


def _balanced_parts(sentence: str) -> list[str]:
    """긴 문장을 비슷한 길이로 나눈다. 짧은 꼬리("왔어요.")만 따로 남지 않게 한다."""
    words, parts = sentence.split(), []
    count = math.ceil(len(sentence) / _LINE_CHARS)
    target = len(sentence) / count
    line = ""
    for w in words:
        if line and len(parts) < count - 1 and len(line) + 1 + len(w) > target + 4:
            parts.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    return [*parts, line] if line else parts


def split_script(text: str, limit: int | None = MAX_SCRIPT_LINES) -> list[str]:
    """문장 단위로 나누고 긴 문장은 비슷한 길이로 잘라 자막 줄로 만든다. limit=None 이면 자르지 않는다."""
    lines = []
    for sentence in re.split(r"(?<=[.!?。！？~])\s+|\n+", text.strip()):
        sentence = " ".join(sentence.split())
        if sentence:
            lines.extend(_balanced_parts(sentence) if len(sentence) > _LINE_CHARS else [sentence])
    return lines if limit is None else lines[:limit]


def merchant_script_fallback(text: str, langs: list[str], limit: int | None = MAX_SCRIPT_LINES) -> dict[str, list[str]]:
    """번역이 안 되면 다른 언어 자막도 한국어 원문을 쓴다. limit=None 이면 줄 수로 자르지 않는다(목소리 영상)."""
    ko = split_script(text, limit) or [text.strip()[:_LINE_CHARS]]
    return {"ko": ko, **{lang: ko for lang in langs if lang != "ko"}}


def _same_length_lines(data: dict, lang: str, n: int) -> list[str] | None:
    lines = data.get(lang)
    if isinstance(lines, list) and len(lines) == n and all(isinstance(x, str) and x.strip() for x in lines):
        return [x.strip()[:80] for x in lines]
    return None


async def merchant_script(
    llm: LLMProvider, text: str, langs: list[str], limit: int | None = MAX_SCRIPT_LINES
) -> dict[str, list[str]]:
    """사장님 문장은 그대로 줄만 나눠 한국어 자막으로 쓰고, AI 는 번역만 한다. 실패하면 다른 언어도 한국어 원문."""
    script = merchant_script_fallback(text, langs, limit)
    if is_mock(llm):
        return script
    ko = script["ko"]
    try:
        data = await complete_json(llm, TRANSLATE_LINES_PROMPT, json.dumps({"langs": langs, "lines": ko}, ensure_ascii=False))
    except (LLMError, ValueError) as e:
        logger.warning("스크립트 자막 번역 실패, 원문으로 대체: %s", type(e).__name__)
        return script
    return {"ko": ko, **{lang: _same_length_lines(data, lang, len(ko)) or ko for lang in langs}}
