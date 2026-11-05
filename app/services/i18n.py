"""언어 선택과 음식 용어집. 번역이 없거나 실패하면 한국어 원문을 쓴다."""

from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Translation

Lang = Literal["ko", "en", "zh"]
LANGS: tuple[str, ...] = ("ko", "en", "zh")
TRANSLATED_LANGS: tuple[str, ...] = ("en", "zh")

PAYMENTS = ("cash", "card", "foreign_card", "onnuri")
PAYMENT_KO = {"cash": "현금", "card": "카드", "foreign_card": "해외카드", "onnuri": "온누리상품권"}

# 키: 한국어 음식 이름. 값: 맵기, 언어별 (이름, 설명, 먹는 법)
# 목 모드 번역과 LLM 번역 실패 시 대체용. 긴 이름부터 매칭한다.
GLOSSARY: dict[str, dict] = {
    "기름떡볶이": {
        "spicy": 1,
        "en": ("Oil Tteokbokki", "Rice cakes stir-fried in oil with chili flakes — crispy outside, chewy inside.", "Eat while hot. Ask for red (spicy) or soy (mild)."),
        "zh": ("油炒年糕", "用油和辣椒粉翻炒的年糕，外脆里糯。", "趁热吃，可选辣味或酱油味。"),
    },
    "간장떡볶이": {
        "spicy": 0,
        "en": ("Soy Sauce Tteokbokki", "Rice cakes stir-fried in a sweet soy sauce glaze. Not spicy.", "Good choice if you don't eat spicy food."),
        "zh": ("酱油炒年糕", "用甜酱油炒的年糕，不辣。", "不吃辣的人可以选这个。"),
    },
    "라볶이": {
        "spicy": 2,
        "en": ("Rabokki", "Tteokbokki with ramen noodles in sweet-spicy gochujang sauce.", "Eat the noodles first before they get soggy."),
        "zh": ("拉面炒年糕", "加了拉面的甜辣炒年糕。", "先吃面，免得泡软。"),
    },
    "떡볶이": {
        "spicy": 2,
        "en": ("Tteokbokki", "Chewy rice cakes in a sweet and spicy gochujang sauce.", "Use a toothpick. Dip fried snacks into the sauce."),
        "zh": ("炒年糕", "甜辣酱炒的软糯年糕。", "用牙签吃，炸物可以蘸酱。"),
    },
    "순대": {
        "spicy": 0,
        "en": ("Sundae (Korean Sausage)", "Steamed sausage filled with glass noodles and pork blood.", "Dip in salt-pepper mix or tteokbokki sauce."),
        "zh": ("韩式血肠", "灌入粉条和猪血的蒸肠。", "蘸椒盐或炒年糕酱吃。"),
    },
    "튀김": {
        "spicy": 0,
        "en": ("Twigim (Korean Fritters)", "Deep-fried vegetables, squid, dumplings and seaweed rolls.", "Dip in tteokbokki sauce or soy sauce."),
        "zh": ("韩式炸物", "炸蔬菜、鱿鱼、饺子和紫菜卷。", "蘸炒年糕酱或酱油吃。"),
    },
    "모둠튀김": {
        "spicy": 0,
        "en": ("Assorted Twigim (Fritters)", "Mixed deep-fried vegetables, squid, dumplings and seaweed rolls.", "Dip in tteokbokki sauce."),
        "zh": ("炸物拼盘", "炸蔬菜、鱿鱼、饺子和紫菜卷的拼盘。", "蘸炒年糕酱吃。"),
    },
    "꼬마김밥": {
        "spicy": 0,
        "en": ("Mini Gimbap", "Bite-sized seaweed rice rolls.", "Dip in mustard soy sauce."),
        "zh": ("迷你紫菜包饭", "一口大小的紫菜包饭。", "蘸芥末酱油吃。"),
    },
    "김밥": {
        "spicy": 0,
        "en": ("Gimbap", "Rice and vegetables rolled in seaweed.", "Eat by hand or with chopsticks. Great to go."),
        "zh": ("紫菜包饭", "用紫菜卷米饭和蔬菜。", "用手或筷子吃，适合外带。"),
    },
    "어묵": {
        "spicy": 0,
        "en": ("Eomuk (Fish Cake Skewer)", "Fish cake on a skewer simmered in hot broth.", "The broth is free — drink it from a paper cup."),
        "zh": ("鱼饼串", "在热汤里煮的鱼饼串。", "汤是免费的，用纸杯喝。"),
    },
    "꽈배기": {
        "spicy": 0,
        "en": ("Kkwabaegi (Twisted Doughnut)", "Twisted fried dough coated with sugar.", "Best eaten warm and fresh."),
        "zh": ("韩式麻花", "裹糖的扭结炸面圈。", "趁热吃最好。"),
    },
    "찹쌀도넛": {
        "spicy": 0,
        "en": ("Sweet Rice Doughnut", "Chewy glutinous rice doughnut filled with red bean paste.", "Best eaten warm."),
        "zh": ("糯米甜甜圈", "红豆馅的软糯甜甜圈。", "趁热吃最好。"),
    },
    "호떡": {
        "spicy": 0,
        "en": ("Hotteok (Sweet Pancake)", "Pan-fried dough filled with brown sugar, cinnamon and nuts.", "Careful — the syrup inside is very hot."),
        "zh": ("糖饼", "包红糖、肉桂和坚果的煎饼。", "小心，里面的糖浆很烫。"),
    },
    "빈대떡": {
        "spicy": 0,
        "en": ("Bindaetteok (Mung Bean Pancake)", "Crispy pancake of ground mung beans, pork and vegetables.", "Dip in soy-onion sauce."),
        "zh": ("绿豆煎饼", "绿豆、猪肉和蔬菜做的酥脆煎饼。", "蘸酱油洋葱汁吃。"),
    },
    "만두": {
        "spicy": 0,
        "en": ("Mandu (Dumplings)", "Dumplings filled with pork, tofu and vegetables.", "Dip in soy sauce with vinegar."),
        "zh": ("韩式饺子", "猪肉、豆腐和蔬菜馅的饺子。", "蘸醋酱油吃。"),
    },
    "고기완자": {
        "spicy": 0,
        "en": ("Meatball Pancake", "Pan-fried patties of minced pork and tofu.", "Eat hot with soy sauce."),
        "zh": ("肉丸煎饼", "猪肉和豆腐做的煎肉饼。", "趁热蘸酱油吃。"),
    },
    "식혜": {
        "spicy": 0,
        "en": ("Sikhye (Sweet Rice Drink)", "Cold, sweet traditional drink made from rice and malt.", "Drink it cold."),
        "zh": ("甜米露", "用米和麦芽做的冰甜传统饮料。", "冰着喝。"),
    },
    "닭강정": {
        "spicy": 1,
        "en": ("Dakgangjeong (Sweet Crispy Chicken)", "Fried chicken bites glazed in sweet sauce.", "Great to take away."),
        "zh": ("韩式甜炸鸡", "裹甜酱的炸鸡块。", "适合外带。"),
    },
    # 작품에만 나오는 음식 (P1 K-콘텐츠 표시용)
    "라면": {
        "spicy": 2,
        "en": ("Ramyeon (Korean Instant Noodles)", "Spicy Korean instant noodles.", ""),
        "zh": ("韩式拉面", "辣味韩式方便面。", ""),
    },
    "국밥": {
        "spicy": 0,
        "en": ("Gukbap (Rice Soup)", "Hearty soup served with rice.", ""),
        "zh": ("汤饭", "配米饭的浓汤。", ""),
    },
    "비빔밥": {
        "spicy": 1,
        "en": ("Bibimbap (Mixed Rice)", "Rice topped with vegetables and gochujang, mixed before eating.", "Mix everything well before eating."),
        "zh": ("拌饭", "米饭配蔬菜和辣椒酱，拌匀后吃。", "吃之前拌匀。"),
    },
    "나물": {
        "spicy": 0,
        "en": ("Namul (Seasoned Vegetables)", "Seasoned vegetable side dishes.", ""),
        "zh": ("凉拌野菜", "调味的蔬菜小菜。", ""),
    },
    "한우": {
        "spicy": 0,
        "en": ("Hanwoo (Korean Beef)", "Premium Korean native beef.", ""),
        "zh": ("韩牛", "韩国本土优质牛肉。", ""),
    },
    "짜파구리": {
        "spicy": 0,
        "en": ("Jjapaguri (Ram-don)", "Black bean noodles mixed with spicy ramen.", ""),
        "zh": ("炸酱拉面", "炸酱面和辣拉面混在一起的面。", ""),
    },
    "달고나": {
        "spicy": 0,
        "en": ("Dalgona (Honeycomb Toffee)", "Crunchy candy made of melted sugar and baking soda.", ""),
        "zh": ("椪糖", "用白糖和小苏打做的脆糖。", ""),
    },
}

# 작품 음식 → 시장에 없을 때 비슷한 음식 (P1)
SIMILAR_FOODS: dict[str, list[str]] = {
    "짜파구리": ["라볶이", "떡볶이"],
    "라면": ["라볶이", "떡볶이"],
    "국밥": ["순대"],
    "비빔밥": ["김밥"],
    "나물": ["김밥"],
    "달고나": ["꽈배기", "호떡"],
    "치킨": ["닭강정"],
    "전": ["빈대떡"],
}

_KEYS = sorted(GLOSSARY, key=len, reverse=True)


def glossary_entry(name_ko: str) -> dict | None:
    compact = name_ko.replace(" ", "")
    for key in _KEYS:
        if key in compact:
            return GLOSSARY[key]
    return None


def glossary_translate(name_ko: str, lang: str) -> dict:
    """{name, description, how_to_eat}. 용어집에 없으면 한국어 이름만."""
    entry = glossary_entry(name_ko)
    if not entry or lang not in entry:
        return {"name": name_ko, "description": "", "how_to_eat": ""}
    name, desc, how = entry[lang]
    # "할매 떡볶이" 처럼 수식어가 붙은 이름은 원문을 같이 보여준다.
    if name_ko.replace(" ", "") not in GLOSSARY:
        name = f"{name} ({name_ko})"
    return {"name": name, "description": desc, "how_to_eat": how}


def food_name(name_ko: str, lang: str) -> str:
    return name_ko if lang == "ko" else glossary_translate(name_ko, lang)["name"]


def load_translations(db: Session, target: str, ids: list[int], lang: str) -> dict[int, Translation]:
    if lang == "ko" or not ids:
        return {}
    rows = db.scalars(
        select(Translation).where(Translation.target == target, Translation.target_id.in_(ids), Translation.lang == lang)
    )
    return {t.target_id: t for t in rows}


def save_translations(db: Session, target: str, target_id: int, by_lang: dict[str, dict]) -> None:
    """by_lang: {"en": {name, description, how_to_eat}, ...}. 기존 값을 덮어쓴다."""
    db.query(Translation).filter(Translation.target == target, Translation.target_id == target_id).delete()
    for lang, t in by_lang.items():
        db.add(
            Translation(
                target=target,
                target_id=target_id,
                lang=lang,
                name=t.get("name", ""),
                description=t.get("description", ""),
                how_to_eat=t.get("how_to_eat", ""),
            )
        )
