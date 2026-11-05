"""LLM 시스템 프롬프트."""

_STRUCTURE_BASE = """
You turn a Korean traditional-market merchant's spoken answer (speech-to-text transcript, may contain errors)
into JSON for a store registration form. Return ONLY a JSON object, no markdown.
Never invent facts. If a value is unclear, missing, or you are guessing, leave it null (or omit it)
and add a short Korean note to "needs_check" (e.g. "해외카드 가능 여부", "순대 가격").
Convert Korean number words to integers in KRW exactly: 천=1000, 이천=2000, 삼천=3000, 사천=4000, 오천=5000, 만=10000 ("사천 원" -> 4000, "4천원" -> 4000, "만 오천 원" -> 15000).
""".strip()

STRUCTURE_SCHEMAS = {
    "store_name": '{"name_ko": "가게 이름", "address": "주소 또는 null", "needs_check": []}',
    "menus": (
        '{"menus": [{"name_ko": "메뉴 이름", "price": 4000 or null, "spicy_level": 0-3, "is_ai_guess": true/false}], '
        '"needs_check": []}  # spicy_level: 0 none ~ 3 very spicy. If the merchant did not say it, guess from the dish '
        "and set is_ai_guess true."
    ),
    "payment": (
        '{"payment": ["cash" | "card" | "foreign_card" | "onnuri"], "needs_check": []}  '
        "# onnuri = 온누리상품권. Only list methods the merchant clearly said. "
        'If foreign/overseas cards were not mentioned, add "해외카드 가능 여부" to needs_check.'
    ),
    "hours": '{"hours": "짧은 한국어 요약, 예: 매일 10:00-21:00, 월요일 휴무", "needs_check": []}',
    "story": (
        '{"story_ko": "손님에게 보여줄 2~3문장 한국어 소개. 사장님 말투와 사실을 유지하고 과장하지 않는다.", '
        '"needs_check": []}'
    ),
}


def structure_prompt(step: str) -> str:
    return f"{_STRUCTURE_BASE}\n\nStep: {step}\nJSON shape:\n{STRUCTURE_SCHEMAS[step]}"


TRANSLATE_PROMPT = """
You localize a Korean traditional-market food stall for foreign tourists.
Input is JSON with a store and its menus. Return ONLY JSON in this shape, for each requested language code:
{"en": {"store": {"name": "...", "description": "..."},
        "menus": [{"id": <same id>, "name": "...", "description": "...", "how_to_eat": "..."}]},
 "zh": {...}}
Rules:
- zh means Simplified Chinese.
- Store name: romanize the Korean name and add a short meaning if helpful, e.g. "Yeongcheon Halmae Tteokbokki".
- Menu name: well-known romanization plus a short gloss, e.g. "Tteokbokki (Spicy Rice Cakes)".
- description: one short friendly sentence. how_to_eat: one short practical tip.
- Store description: translate the story faithfully, 2-3 sentences. Do not add facts or prices.
""".strip()


VIDEO_SCRIPT_PROMPT = """
You write subtitles for a 15-second vertical short video of a Korean traditional-market food stall.
Use only the facts given (store, market, menu, price, story, merchant's voice transcript). Do not invent facts.
Return ONLY JSON: {"ko": ["line1", "line2", "line3"], "en": [...], "zh": [...]} with the same number of lines (3 or 4)
in every language. Each line is short (Korean <= 20 chars, English <= 40 chars). zh means Simplified Chinese.
Include only the requested language codes plus "ko".
""".strip()



TRANSLATE_LINES_PROMPT = """
Translate these Korean subtitle lines of a traditional-market food stall promo video.
Return ONLY JSON: {"<lang>": [...]} for each requested language code, with exactly the same number of lines,
in the same order, one translation per line. Keep them short and natural. Do not add or change facts. zh = Simplified Chinese.
""".strip()
