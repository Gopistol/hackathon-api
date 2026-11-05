"""K-콘텐츠 카드 이미지(더미)를 만든다. 포스터·스틸컷 대신 직접 만든 텍스트 카드.

    .venv/bin/python scripts/make_content_cards.py   → demo/contents/<key>.jpg (커밋 후 배포·리셋)

사용 허가된 이미지가 생기면 같은 파일 이름으로 바꿔 넣으면 된다.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.seed_data import CONTENTS  # noqa: E402
from app.services.i18n import food_name  # noqa: E402

W, H = 720, 960
ACCENT = (155, 74, 47)  # #9B4A2F
DARK = (42, 20, 13)
CREAM = (250, 240, 228)
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
LATIN = "/System/Library/Fonts/Helvetica.ttc"  # é 같은 라틴 문자용 (한글 폰트에 없음)


def font(size: int, bold: bool = True):
    try:
        return ImageFont.truetype(FONT, size, index=6 if bold else 0)
    except OSError:
        return ImageFont.load_default()


def latin(size: int):
    try:
        return ImageFont.truetype(LATIN, size)
    except OSError:
        return font(size, False)


def wrap(draw, text, f, width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=f) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    return [*lines, line] if line else lines


def card(c: dict, dst: Path) -> None:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):  # 위 적토색 → 아래 진한 갈색
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(ACCENT, DARK)))
    # 장식 원
    d.ellipse([W - 300, -140, W + 120, 280], fill=(176, 96, 66))
    d.ellipse([-160, H - 360, 220, H + 20], fill=(70, 34, 22))

    d.text((48, 52), f"{c['platform']} · {c['genre']}".upper(), font=font(28, False), fill=CREAM)
    y = 300
    for line in wrap(d, c["title"], font(76), W - 96):
        d.text((48, y), line, font=font(76), fill="white")
        y += 92
    for line in wrap(d, c["i18n"]["en"], latin(36), W - 96):
        d.text((48, y + 8), line, font=latin(36), fill=CREAM)
        y += 46

    y += 60
    d.text((48, y), "작품 속 음식 · Food in the show", font=font(26, False), fill=CREAM)
    y += 46
    x = 48
    for food in c["foods"]:
        label = f"{food}  {food_name(food, 'en').split(' (')[0]}"
        f = font(30)
        w = int(d.textlength(label, font=f)) + 44
        if x + w > W - 48:
            x, y = 48, y + 70
        d.rounded_rectangle([x, y, x + w, y + 56], radius=28, fill=CREAM)
        d.text((x + 22, y + 28), label, font=f, fill=ACCENT, anchor="lm")
        x += w + 14

    d.text((48, H - 56), "예시 이미지 · 공식 포스터/스틸컷 아님", font=font(22, False), fill=(220, 200, 185))
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "JPEG", quality=88)


if __name__ == "__main__":
    for c in CONTENTS:
        out = ROOT / "demo" / "contents" / f"{c['key']}.jpg"
        card(c, out)
        print("ok", out.name, out.stat().st_size // 1024, "KB")
