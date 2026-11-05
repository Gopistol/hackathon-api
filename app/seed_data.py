"""시연용 시드 데이터. 가게 이름은 가상, 시장은 실제(서대문구).

시장 좌표는 demo/markets.csv(전국전통시장표준데이터)가 있으면 그 값을 쓰고, 없으면 아래 대체값을 쓴다.
"""

# 시장 이름(표준데이터의 시장명) → 외국어 표기
MARKET_I18N = {
    "독립문영천시장": {"en": "Dongnimmun Yeongcheon Market", "zh": "独立门灵泉市场"},
    "영천시장": {"en": "Yeongcheon Market", "zh": "灵泉市场"},
    "포방터시장": {"en": "Pobangteo Market", "zh": "Pobangteo市场"},
}

# CSV 가 없을 때 쓰는 대체값. 좌표는 대략값이라 CSV 를 받으면 바뀐다.
FALLBACK_MARKETS = [
    {
        "name": "독립문영천시장",
        "address": "서울특별시 서대문구 통일로 189",
        "lat": 37.5717,
        "lng": 126.9607,
        "open_cycle": "상설",
    },
    {
        "name": "포방터시장",
        "address": "서울특별시 서대문구 홍은동",
        "lat": 37.5906,
        "lng": 126.9440,
        "open_cycle": "상설",
    },
]

# 시드 가게가 속할 시장. 표준데이터 이름이 다를 수 있어 포함 여부로 찾는다.
YEONGCHEON, POBANGTEO = "영천", "포방터"

STORES = [
    {
        "key": "yeongcheon-oil",
        "market": YEONGCHEON,
        "name": "영천 할매 기름떡볶이",
        "menus": [("기름떡볶이", 4000), ("간장떡볶이", 4000), ("어묵", 1000)],
        "payment": ["cash", "card", "onnuri"],
        "hours": "매일 10:00-21:00, 월요일 휴무",
        "story_ko": "40년 동안 영천시장에서 기름떡볶이만 만들어 왔어요. 고춧가루와 기름으로 바삭하게 볶아 드려요.",
        "i18n": {
            "en": ("Yeongcheon Halmae Oil Tteokbokki", "We have made only oil tteokbokki at Yeongcheon Market for 40 years. Stir-fried crispy with chili flakes and oil."),
            "zh": ("灵泉奶奶油炒年糕", "我们在灵泉市场做油炒年糕已经40年了。用辣椒粉和油炒得外脆里糯。"),
        },
        "script": {
            "ko": ["영천시장 40년 기름떡볶이", "바삭하게 볶은 기름떡볶이 4,000원", "매운맛·간장맛 골라 드세요"],
            "en": ["40 years of oil tteokbokki", "Crispy Oil Tteokbokki ₩4,000", "Choose spicy or soy sauce"],
            "zh": ["灵泉市场40年油炒年糕", "外脆里糯油炒年糕 ₩4,000", "可选辣味或酱油味"],
        },
    },
    {
        "key": "yeongcheon-donut",
        "market": YEONGCHEON,
        "name": "영천 꽈배기",
        "menus": [("꽈배기", 1000), ("찹쌀도넛", 1000), ("식혜", 2000)],
        "payment": ["cash", "card", "foreign_card"],
        "hours": "매일 09:00-20:00",
        "story_ko": "매일 아침 직접 반죽해서 하루 종일 갓 튀긴 꽈배기를 팔아요.",
        "i18n": {
            "en": ("Yeongcheon Kkwabaegi", "We knead the dough every morning and fry fresh twisted doughnuts all day."),
            "zh": ("灵泉麻花", "每天早上亲手和面，全天现炸麻花。"),
        },
        "script": {
            "ko": ["매일 아침 직접 반죽", "갓 튀긴 꽈배기 1,000원", "따뜻할 때 드세요"],
            "en": ["Dough made fresh every morning", "Twisted doughnut ₩1,000", "Best eaten warm"],
            "zh": ["每天早上亲手和面", "现炸麻花 ₩1,000", "趁热吃最好"],
        },
    },
    {
        "key": "dongnimmun-bunsik",
        "market": YEONGCHEON,
        "name": "독립문 분식",
        "menus": [("순대", 5000), ("김밥", 3000), ("모둠튀김", 4000)],
        "payment": ["cash", "card"],
        "hours": "10:30-20:30, 일요일 휴무",
        "story_ko": "독립문 앞에서 25년째, 순대와 김밥을 매일 새로 만들어요.",
        "i18n": {
            "en": ("Dongnimmun Bunsik (Snack Bar)", "For 25 years near Dongnimmun (Independence Gate), we have made sundae and gimbap fresh every day."),
            "zh": ("独立门小吃", "在独立门附近开了25年，每天新鲜制作血肠和紫菜包饭。"),
        },
        "script": {
            "ko": ["독립문 앞 25년 분식집", "순대 5,000원 · 김밥 3,000원", "매일 새로 만들어요"],
            "en": ["25-year snack bar by Dongnimmun", "Sundae ₩5,000 · Gimbap ₩3,000", "Made fresh every day"],
            "zh": ["独立门前25年的小吃店", "血肠 ₩5,000 · 紫菜包饭 ₩3,000", "每天新鲜制作"],
        },
    },
    {
        "key": "pobangteo-tteokbokki",
        "market": POBANGTEO,
        "name": "포방터 떡볶이",
        "menus": [("떡볶이", 4000), ("라볶이", 5000), ("모둠튀김", 4000)],
        "payment": ["cash", "card", "foreign_card", "onnuri"],
        "hours": "매일 11:00-21:00",
        "story_ko": "포방터시장 골목에서 달콤하고 매콤한 옛날 떡볶이를 만들어요. 튀김을 떡볶이 국물에 찍어 드셔 보세요.",
        "i18n": {
            "en": ("Pobangteo Tteokbokki", "Old-style sweet and spicy tteokbokki in an alley of Pobangteo Market. Try dipping fritters in the sauce."),
            "zh": ("Pobangteo炒年糕", "在Pobangteo市场的小巷里做甜辣的传统炒年糕。试试把炸物蘸着酱吃。"),
        },
        "script": {
            "ko": ["포방터시장 옛날 떡볶이", "달콤 매콤 떡볶이 4,000원", "튀김은 국물에 찍어 드세요"],
            "en": ["Old-style tteokbokki", "Sweet & spicy Tteokbokki ₩4,000", "Dip fritters in the sauce"],
            "zh": ["Pobangteo市场传统炒年糕", "甜辣炒年糕 ₩4,000", "炸物蘸酱更好吃"],
        },
    },
    {
        "key": "hongeun-bindaetteok",
        "market": POBANGTEO,
        "name": "홍은동 빈대떡",
        "menus": [("빈대떡", 6000), ("고기완자", 3000), ("식혜", 2000)],
        "payment": ["cash", "card"],
        "hours": "12:00-22:00, 화요일 휴무",
        "story_ko": "맷돌에 간 녹두로 빈대떡을 부쳐요. 비 오는 날 특히 인기예요.",
        "i18n": {
            "en": ("Hongeun-dong Bindaetteok", "We pan-fry mung bean pancakes from beans ground on a millstone. Especially popular on rainy days."),
            "zh": ("弘恩洞绿豆煎饼", "用石磨磨的绿豆做煎饼，下雨天特别受欢迎。"),
        },
        "script": {
            "ko": ["맷돌에 간 녹두 빈대떡", "바삭한 빈대떡 6,000원", "비 오는 날 더 맛있어요"],
            "en": ["Millstone-ground mung beans", "Crispy Bindaetteok ₩6,000", "Even better on rainy days"],
            "zh": ["石磨绿豆煎饼", "酥脆绿豆煎饼 ₩6,000", "下雨天更好吃"],
        },
    },
]

CONTENTS = [
    {
        "title": "이상한 변호사 우영우",
        "i18n": {"en": "Extraordinary Attorney Woo", "zh": "非常律师禹英禑"},
        "platform": "Netflix",
        "genre": "Drama",
        "foods": ["김밥"],
    },
    {
        "title": "기생충",
        "i18n": {"en": "Parasite", "zh": "寄生虫"},
        "platform": "Movie",
        "genre": "Film",
        "foods": ["짜파구리"],
    },
    {
        "title": "오징어 게임",
        "i18n": {"en": "Squid Game", "zh": "鱿鱼游戏"},
        "platform": "Netflix",
        "genre": "Drama",
        "foods": ["달고나"],
    },
]
