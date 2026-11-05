"""시연용 미리 렌더링 영상을 만든다 (로컬에서 실행 후 demo/ 를 커밋).

    .venv/bin/python scripts/make_demo_media.py

demo/photos/<가게 key>/ 에 실제 사진(jpg/png 3~5장)을 넣으면 그 사진으로 만든다.
폴더가 비어 있으면 색 배경에 메뉴 이름을 쓴 임시 사진을 만든다.
"""

import asyncio
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.seed_data import STORES  # noqa: E402
from app.services.video import make_thumbnail, prepare_photo, render  # noqa: E402

DEMO = Path(__file__).resolve().parents[1] / "demo"
COLORS = [((214, 69, 46), (247, 160, 72)), ((38, 70, 140), (92, 160, 220)), ((44, 120, 80), (150, 200, 110))]
FONT_CANDIDATES = ["/System/Library/Fonts/AppleSDGothicNeo.ttc", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"]


def _font(size: int):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def placeholder(store: dict, i: int, dst: Path) -> None:
    top, bottom = COLORS[i % len(COLORS)]
    img = Image.new("RGB", (1080, 1920))
    draw = ImageDraw.Draw(img)
    for y in range(1920):
        t = y / 1919
        draw.line([(0, y), (1080, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    name, price = store["menus"][i % len(store["menus"])]
    draw.text((540, 820), name, font=_font(150), fill="white", anchor="mm")
    draw.text((540, 1010), f"{price:,}원", font=_font(90), fill="white", anchor="mm")
    draw.text((540, 1600), store["name"], font=_font(64), fill=(255, 255, 255, 200), anchor="mm")
    draw.text((540, 1700), "임시 이미지 · 실제 사진으로 교체", font=_font(40), fill="white", anchor="mm")
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "JPEG", quality=88)


async def main() -> None:
    for store in STORES:
        src = DEMO / "photos" / store["key"]
        files = sorted(p for p in src.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png")) if src.exists() else []
        if len(files) < 3:
            files = []
            for i in range(3):
                placeholder(store, i, src / f"{i + 1}.jpg")
                files.append(src / f"{i + 1}.jpg")
        work = DEMO / ".work" / store["key"]
        prepared = []
        for i, f in enumerate(files[:5]):
            prepare_photo(f.read_bytes(), work / f"photo_{i}.jpg")
            prepared.append(work / f"photo_{i}.jpg")
        out = DEMO / "media" / "videos" / f"demo-{store['key']}.mp4"
        await render(prepared, out, 15.0)
        make_thumbnail(prepared[0], DEMO / "media" / "thumbs" / f"demo-{store['key']}.jpg")
        print("ok", out.name, out.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    asyncio.run(main())
