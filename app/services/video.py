"""숏폼 생성: 사진 1~5장 + (선택) 사장님 녹음·스크립트 → 언어별 720x1280 MP4 + 썸네일 + 언어별 VTT.

생성형 영상 모델 없이 ffmpeg 템플릿 합성만 쓴다.
- 장면: 사진마다 카메라 움직임을 바꾸고 장면 사이를 부드럽게 전환한다.
- 자막: 영상에 직접 넣는다. 그래서 언어마다 영상을 따로 만든다. 플레이어용 VTT 도 같이 남긴다.
  시작에 가게 이름, 끝에 메뉴·가격·결제 카드를 넣는다 (DB 값만 쓴다).
- 소리: 녹음이 있으면 한국어 영상은 사장님 원래 목소리, 외국어 영상은 사장님 목소리를 복제해 더빙한다.
  복제 더빙(ElevenLabs) → AI 기본 목소리(OpenAI) → 원래 목소리 순서로 대체한다.
  녹음이 없거나 읽을 수 없으면 소리 없는 영상이다.
"""

import asyncio
import io
import json
import logging
import math
import re
import shutil
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import SessionLocal
from app.llm import LLMError, LLMProvider
from app.models import Menu, Store, Video
from app.services import ai, elevenlabs, subtitles
from app.services.i18n import PAYMENT_KO, load_translations

logger = logging.getLogger(__name__)

W, H, FPS = 720, 1280, 30
MIN_SECONDS, MAX_SECONDS = 20.0, 30.0
SECONDS_PER_LINE = 5.0  # 자막 한 줄이 화면에 머무는 시간
MIN_SECONDS_PER_LINE = 4.0
MAX_SEGMENT = 7.0  # 사진 한 장이 한 가지 움직임으로 보이는 최대 시간. 넘으면 다른 움직임으로 나눈다
RENDER_TIMEOUT = 90  # 언어 하나당
MAX_PIXELS = 48_000_000  # 8000x6000. 디코딩 메모리(RGB 약 144MB)를 서버 600MB 안에 둔다
STT_BUDGET = 10  # 초. 영상 자막용 음성 인식이 늦으면 건너뛴다
SCRIPT_BUDGET = 12  # 초. 자막 생성이 늦으면 템플릿 문장을 쓴다
DUB_BUDGET = 25  # 초. 목소리 복제·더빙이 늦으면 다음 대체 수단으로
ALIGN_BUDGET = 15  # 초. 자막 시간 맞추기가 늦으면 글자 수 비율로 나눈다
TAIL_SECONDS = 1.5  # 말이 끝난 뒤 정보 카드를 읽을 여유
XFADE = 0.45  # 장면 전환 길이(초)

# 메모리 600M·CPU 2개 서버라 렌더링은 한 번에 하나만
_render_lock = asyncio.Semaphore(1)


class RecordingTooLong(Exception):
    """녹음을 글로 바꿨더니 30초(자막 7줄)를 넘음. 뒷부분을 버리지 않고 다시 녹음을 요청한다."""


def ffmpeg_exe() -> str:
    s = get_settings()
    if s.ffmpeg_path:
        return s.ffmpeg_path
    if found := shutil.which("ffmpeg"):
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def work_dir(video_id: int) -> Path:
    return Path(get_settings().audio_dir).parent / "work" / str(video_id)


def prepare_photo(data: bytes, dst: Path) -> None:
    """회전 보정 후 1080x1920 JPEG 로 저장. 이미지가 아니면 AppError.

    세로 사진은 가운데를 잘라 화면을 채우고, 가로·정사각 사진은 음식이 잘리지 않게
    같은 사진을 흐리게 깐 배경 위에 통째로 올린다."""
    try:
        img = Image.open(io.BytesIO(data))  # 여기까지는 헤더만 읽는다
        if img.width * img.height > MAX_PIXELS:
            raise AppError("IMAGE_TOO_LARGE", "사진 해상도가 너무 커요. 4800만 화소 이하로 올려 주세요.", 413)
        img.draft("RGB", (1080, 1920))  # JPEG 는 필요한 크기로 줄여서 디코딩해 메모리를 아낀다
        img = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise AppError("INVALID_IMAGE", "사진 파일을 읽을 수 없어요. JPG나 PNG로 올려 주세요.", 422) from e
    if img.width / img.height <= 0.8:
        out = ImageOps.fit(img, (1080, 1920), Image.Resampling.LANCZOS)
    else:
        out = ImageOps.fit(img, (270, 480)).filter(ImageFilter.GaussianBlur(12)).resize((1080, 1920))
        out = ImageEnhance.Brightness(out).enhance(0.55)
        photo = ImageOps.contain(img, (1080, 1920), Image.Resampling.LANCZOS)
        out.paste(photo, ((1080 - photo.width) // 2, (1920 - photo.height) // 2))
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, "JPEG", quality=90)


def make_thumbnail(photo: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(photo) as img:
        img.resize((W, H), Image.Resampling.LANCZOS).save(dst, "JPEG", quality=85)


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def write_vtt(lines: list[str], duration: float, dst: Path) -> None:
    """줄마다 같은 시간씩 나눠 보여준다. (시드한 시연 영상용)"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    step = duration / max(len(lines), 1)
    cues = [f"{i + 1}\n{_ts(i * step)} --> {_ts((i + 1) * step - 0.05)}\n{line}" for i, line in enumerate(lines)]
    dst.write_text("WEBVTT\n\n" + "\n\n".join(cues) + "\n", encoding="utf-8")


def video_duration(n_lines: int) -> float:
    """소리 없는 영상 길이 (노션 04 계약). 줄당 5초, 30초를 넘으면 줄당 4초까지 줄인다. 20초보다 짧으면 20초."""
    seconds = n_lines * SECONDS_PER_LINE
    if seconds > MAX_SECONDS:
        seconds = n_lines * MIN_SECONDS_PER_LINE
    return min(max(seconds, MIN_SECONDS), MAX_SECONDS)


def plan_segments(n_photos: int, duration: float) -> list[tuple[int, float]]:
    """[(사진 번호, 초)]. 사진이 적어 한 장이 오래 보이면 여러 구간으로 나눠 움직임을 바꾼다."""
    per = duration / n_photos
    k = math.ceil(per / MAX_SEGMENT)
    return [(i, per / k) for i in range(n_photos) for _ in range(k)]


# 구간마다 돌아가며 쓰는 카메라 움직임. P 는 0→1 진행률, E 는 천천히 시작·끝나는 곡선.
_E = "(P*P*(3-2*P))"
_MOTIONS = [
    ("1+0.12*{E}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),  # 다가가기
    ("1.12", "(iw-iw/zoom)*{E}", "ih/2-(ih/zoom/2)"),  # 왼쪽 → 오른쪽
    ("1.12-0.12*{E}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),  # 멀어지기
    ("1.12", "(iw-iw/zoom)*(1-{E})", "ih/2-(ih/zoom/2)"),  # 오른쪽 → 왼쪽
    ("1.12", "iw/2-(iw/zoom/2)", "(ih-ih/zoom)*{E}"),  # 위 → 아래
]
# 장면 전환. 너무 화려하지 않은 것만 돌려 쓴다.
_TRANSITIONS = ["smoothleft", "fade", "smoothup", "slideleft", "fadeblack"]


def _clip_filter(i: int, frames: int) -> str:
    e = _E.replace("P", f"(on/{max(frames - 1, 1)})")
    z, x, y = (m.format(E=e) for m in _MOTIONS[i % len(_MOTIONS)])
    return (
        f"[{i}:v]zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS},"
        f"setsar=1,format=yuv420p[v{i}]"
    )


def _filter_path(path: str) -> str:
    """ffmpeg 필터 옵션 안에 넣을 경로. Windows 역슬래시는 / 로 바꾸고 드라이브 콜론은 이스케이프한다."""
    return "'" + path.replace("\\", "/").replace(":", "\\:") + "'"


async def _ffmpeg(args: list[str], timeout: float, cwd: Path | None = None) -> None:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_exe(), "-y", "-hide_banner", *args,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, cwd=cwd,
    )  # fmt: skip
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise RuntimeError("ffmpeg 시간 초과")
    if proc.returncode != 0:
        logger.warning("ffmpeg 실패: %s", stderr.decode(errors="ignore")[-500:])
        raise RuntimeError("ffmpeg 실패")


async def media_seconds(path: Path) -> float | None:
    """ffmpeg 가 읽은 길이(초). 못 읽으면 None. (imageio-ffmpeg 에는 ffprobe 가 없다)"""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_exe(), "-hide_banner", "-i", str(path), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await asyncio.wait_for(proc.communicate(), 20)
    m = re.search(rb"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


async def to_mp3(src: Path, dst: Path) -> Path | None:
    """녹음을 mp3 로 바꾼다. 브라우저 webm 은 길이 정보가 없어서 한 번 풀어서 다시 쓴다. 못 읽으면 None."""
    try:
        await _ffmpeg(["-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "44100", "-b:a", "128k", str(dst)], 30)
    except RuntimeError:
        return None
    seconds = await media_seconds(dst)
    return dst if seconds and seconds >= 1 else None


async def render(
    photos: list[Path],
    out: Path,
    duration: float,
    voice: Path | None = None,
    background: Path | None = None,
    subtitles_ass: Path | None = None,
) -> float:
    """사진을 이어 붙여 MP4 를 만들고 길이(초)를 돌려준다.

    voice 가 없으면 무음 트랙만 둔다. background 는 AI 기본 목소리로 더빙했을 때 뒤에 작게 까는
    사장님 원래 목소리. subtitles_ass 는 영상에 넣을 자막."""
    s = get_settings()
    segments = plan_segments(len(photos), duration)
    n = len(segments)
    # 전환 구간만큼 장면이 겹치므로 그만큼 각 장면을 늘린다.
    frames = math.ceil((duration + (n - 1) * XFADE) / n * FPS)
    clip = frames / FPS

    args = ["-loglevel", "error", "-nostats"]
    for photo_idx, _ in segments:
        args += ["-i", str(photos[photo_idx].resolve())]
    if voice:
        args += ["-i", str(voice.resolve())]
    else:  # 일부 플레이어 호환을 위해 무음 트랙을 둔다
        args += ["-f", "lavfi", "-t", str(duration), "-i", "anullsrc=r=44100:cl=stereo"]
    if background:
        args += ["-i", str(background.resolve())]

    filters = [_clip_filter(i, frames) for i in range(n)]
    last = "v0"
    for i in range(1, n):
        filters.append(
            f"[{last}][v{i}]xfade=transition={_TRANSITIONS[(i - 1) % len(_TRANSITIONS)]}"
            f":duration={XFADE}:offset={i * (clip - XFADE):.3f}[x{i}]"
        )
        last = f"x{i}"
    chain = f"[{last}]fade=t=out:st={duration - 0.5}:d=0.5"
    if subtitles_ass:
        fonts = f":fontsdir={_filter_path(s.subtitle_fonts_dir)}" if s.subtitle_fonts_dir else ""
        chain += f",subtitles={_filter_path(subtitles_ass.name)}{fonts}"
    filters.append(chain + "[v]")
    if voice:
        # 녹음마다 크기가 달라도 같은 크기로 들리게 맞춘다 (모바일 SNS 기준 -16 LUFS)
        if background:
            mix = f"[{n}:a]apad[a0];[{n + 1}:a]volume=0.15[a1];[a0][a1]amix=inputs=2:duration=first:normalize=0"
        else:
            mix = f"[{n}:a]apad"
        filters.append(mix + ",loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    else:
        filters.append(f"[{n}:a]anull[a]")

    out.parent.mkdir(parents=True, exist_ok=True)
    args += [
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]", "-t", f"{duration:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k" if voice else "32k", "-ar", "44100",
        "-movflags", "+faststart", "-threads", "2",
        str(out.resolve()),
    ]  # fmt: skip
    # 자막 필터 경로를 짧게 쓰려고 자막 파일 폴더에서 실행한다. 다른 경로는 절대경로로 넘겼다.
    await _ffmpeg(args, RENDER_TIMEOUT, cwd=subtitles_ass.parent if subtitles_ass else None)
    return duration


# ---------- 더빙 ----------

DUB_INSTRUCTIONS = {
    "en": "Speak English as a warm, friendly vendor at a Korean traditional market. Natural and upbeat, medium pace.",
    "zh": "用自然亲切的普通话说话，像韩国传统市场里热情的摊主。语速适中。",
}


def _spoken(lines: list[str], lang: str) -> str:
    return ("" if lang == "zh" else " ").join(lines)


async def _speak(llm: LLMProvider, text: str, lang: str, clone_id: str | None) -> tuple[bytes | None, str]:
    """(mp3, 목소리). 복제한 사장님 목소리 → AI 기본 목소리 순서로 시도한다."""
    if clone_id:
        try:
            return await asyncio.wait_for(elevenlabs.speak(text, clone_id), DUB_BUDGET), "clone"
        except (elevenlabs.ElevenLabsError, TimeoutError):
            logger.warning("복제 목소리 더빙 실패 lang=%s, AI 목소리로 대신합니다.", lang)
    speak = getattr(llm, "speak", None)
    if speak and not ai.is_mock(llm):
        try:
            return await asyncio.wait_for(speak(text, DUB_INSTRUCTIONS.get(lang)), DUB_BUDGET), "ai"
        except (LLMError, TimeoutError):
            logger.warning("AI 목소리 더빙 실패 lang=%s, 원래 목소리로 대신합니다.", lang)
    return None, "original"


async def dub(llm: LLMProvider, script: dict[str, list[str]], langs: list[str], voice_ko: Path, wd: Path) -> dict:
    """{lang: (음성 파일, 목소리)}. 더빙이 안 된 언어는 사장님 원래 목소리 + 그 언어 자막으로 만든다."""
    clone_id = None
    if elevenlabs.enabled():
        try:
            clone_id = await asyncio.wait_for(elevenlabs.clone_voice(voice_ko.read_bytes(), voice_ko.name), DUB_BUDGET)
        except (elevenlabs.ElevenLabsError, TimeoutError):
            logger.warning("목소리 복제 실패, AI 목소리로 더빙합니다.")
    try:
        # 네트워크 대기라 언어별로 동시에 한다
        results = await asyncio.gather(*(_speak(llm, _spoken(script[lang], lang), lang, clone_id) for lang in langs))
    finally:
        if clone_id:
            await elevenlabs.delete_voice(clone_id)
    voices = {"ko": (voice_ko, "original")}
    for lang, (mp3, engine) in zip(langs, results):
        if mp3:
            path = wd / f"voice_{lang}.mp3"
            path.write_bytes(mp3)
            voices[lang] = (path, engine)
        else:
            voices[lang] = (voice_ko, "original")
    return voices


async def align(voice: Path, text: str) -> subtitles.Alignment | None:
    """자막 시간을 음성에 맞춘다. 안 되면 None 을 돌려 글자 수 비율로 나누게 한다."""
    if not elevenlabs.enabled():
        return None
    try:
        return await asyncio.wait_for(elevenlabs.align(voice.read_bytes(), voice.name, text), ALIGN_BUDGET)
    except (elevenlabs.ElevenLabsError, TimeoutError):
        logger.warning("자막 시간 맞추기 실패, 글자 수 비율로 나눕니다.")
        return None


# ---------- 이야기 인터뷰 원음 이어 붙이기 ----------

QUOTE_GAP = 0.35  # 구간 사이 쉼(초)


def find_span(chars: list[tuple[str, float, float]], quote: str) -> tuple[float, float] | None:
    """글자별 시간(음성 맞추기 결과)에서 quote 의 (시작초, 끝초). 띄어쓰기는 무시한다."""
    idx = [i for i, (c, _, _) in enumerate(chars) if c.strip()]
    flat = "".join(chars[i][0] for i in idx)
    target = "".join(quote.split())
    pos = flat.find(target) if target else -1
    if pos < 0:
        return None
    return chars[idx[pos]][1], chars[idx[pos + len(target) - 1]][2]


async def stitch_quotes(quotes: list[dict], wd: Path) -> tuple[Path | None, str]:
    """인터뷰 녹음에서 고른 구간만 잘라 이어 붙인다 → (목소리 mp3, 그 목소리의 글).

    구간 시간은 ElevenLabs 음성 맞추기로 찾는다. 못 찾으면 그 답 전체를 쓰고 글도 답 전체로 바꾼다.
    30초를 넘으면 뒤 구간부터 뺀다. 녹음을 못 읽으면 목소리 없이 고른 글만 돌려준다(자막 영상)."""
    def joined(parts: list[str]) -> str:
        return " ".join(t if t[-1] in ".!?~" else f"{t}." for t in parts if t)

    text = joined([q["text"] for q in quotes])
    mp3s: dict[str, Path | None] = {}
    aligned: dict[str, list | None] = {}
    segments: list[tuple[Path, float, float, str]] = []
    for q in quotes:
        src = q.get("audio_path")
        if not src or not Path(src).exists():
            continue
        if src not in mp3s:
            mp3s[src] = await to_mp3(Path(src), wd / f"turn_{len(mp3s)}.mp3")
            aligned[src] = await align(mp3s[src], q["transcript"]) if mp3s[src] else None
        mp3 = mp3s[src]
        if not mp3:
            continue
        span = find_span(aligned[src], q["text"]) if aligned[src] else None
        if span:
            segments.append((mp3, max(span[0] - 0.12, 0.0), span[1] + 0.25, q["text"]))
        elif not any(s[0] == mp3 for s in segments):  # 답 전체는 한 번만
            segments.append((mp3, 0.0, await media_seconds(mp3) or 0.0, q["transcript"]))
    while segments and sum(e - s for _, s, e, _ in segments) + QUOTE_GAP * (len(segments) - 1) > MAX_SECONDS:
        segments.pop()
    if not segments:
        return None, text
    args = ["-loglevel", "error"]
    for mp3, start, end, _ in segments:
        args += ["-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(mp3.resolve())]
    n = len(segments)
    pads = "".join(f"[{i}:a]apad=pad_dur={QUOTE_GAP if i < n - 1 else 0}[a{i}];" for i in range(n))
    out = wd / "voice_ko.mp3"
    try:
        await _ffmpeg(
            [*args, "-filter_complex", pads + "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]",
             "-map", "[a]", "-ac", "1", "-ar", "44100", "-b:a", "128k", str(out.resolve())],
            60,
        )  # fmt: skip
    except RuntimeError:
        return None, text
    return out, joined([seg[3] for seg in segments])


# ---------- 시작 제목·끝 정보 카드 ----------

_PAYMENT = {
    "ko": PAYMENT_KO,
    "en": {"cash": "Cash", "card": "Card", "foreign_card": "Foreign cards", "onnuri": "Onnuri voucher"},
    "zh": {"cash": "现金", "card": "刷卡", "foreign_card": "境外卡", "onnuri": "温努里商品券"},
}
_PAYMENT_TAIL = {"ko": " 가능", "en": " OK", "zh": " 可用"}


def cards(db, store: Store, menu: Menu | None, lang: str) -> tuple[str, list[str]]:
    """(시작 제목, 끝 카드 줄들). 번역이 없으면 한국어 원문."""
    t = load_translations(db, "store", [store.id], lang).get(store.id) if lang != "ko" else None
    title = t.name if t and t.name else store.name
    lines = []
    if menu:
        mt = load_translations(db, "menu", [menu.id], lang).get(menu.id) if lang != "ko" else None
        name = mt.name if mt and mt.name else menu.name_ko
        if menu.price is None:
            lines.append(name)
        else:
            price = {"ko": f"{menu.price:,}원", "zh": f"{menu.price}韩元"}.get(lang, f"₩{menu.price:,}")
            lines.append(f"{name} {price}")
    labels = [_PAYMENT.get(lang, _PAYMENT["en"])[p] for p in store.payment or [] if p in PAYMENT_KO]
    if labels:
        lines.append(" · ".join(labels) + _PAYMENT_TAIL.get(lang, " OK"))
    return title, lines


def script_context(db, store: Store, menu: Menu | None, transcript: str, langs: list[str]) -> dict:
    ctx = {
        "store_name": store.name,
        "market_name": store.market.name,
        "menu_name": menu.name_ko if menu else None,
        "price": menu.price if menu else None,
        "story": store.story_ko,
        "transcript": transcript,
        "i18n": {},
    }
    for lang in langs:
        st = load_translations(db, "store", [store.id], lang).get(store.id)
        mt = load_translations(db, "menu", [menu.id], lang).get(menu.id) if menu else None
        ctx["i18n"][lang] = {
            "store_name": st.name if st else None,
            "story": st.description if st else None,
            "menu_name": mt.name if mt else None,
            "market_name": store.market.name_i18n.get(lang),
        }
    return ctx


async def run_job(video_id: int, langs: list[str], llm: LLMProvider) -> None:
    """BackgroundTasks 로 실행. 입력은 work_dir(video_id) 에 photo_*.jpg, (선택) script.txt, audio.* 로 있다."""
    async with _render_lock:
        db = SessionLocal()
        wd = work_dir(video_id)
        media = get_settings().media_path
        try:
            video = db.get(Video, video_id)
            video.job_status, video.progress = "processing", 5
            db.commit()

            photos = sorted(wd.glob("photo_*.jpg"))
            audio = next(iter(sorted(wd.glob("audio.*"))), None)
            script_file = wd / "script.txt"
            interview_file = wd / "interview.json"
            # 자막 원문: 이야기 인터뷰 구간 → 직접 쓴 스크립트 → 녹음 STT → (없으면) 가게 기본 정보.
            merchant_text = script_file.read_text(encoding="utf-8").strip() if script_file.exists() else ""
            source = "script" if merchant_text else "auto"
            # 녹음은 소리로도 쓴다. 못 읽는 녹음이면 자막에만 쓰고 소리 없는 영상으로 만든다.
            voice_ko = await to_mp3(audio, wd / "voice_ko.mp3") if audio and not merchant_text else None
            if interview_file.exists():
                quotes = json.loads(interview_file.read_text(encoding="utf-8"))["quotes"]
                voice_ko, merchant_text = await stitch_quotes(quotes, wd)
                source = "interview"
            elif not merchant_text and audio:
                try:
                    merchant_text = await asyncio.wait_for(
                        ai.transcribe(llm, audio.read_bytes(), audio.name, "story"), STT_BUDGET
                    )
                    source = "stt" if merchant_text else "auto"
                except (AppError, TimeoutError):
                    pass  # 가게 정보로 만든다
                # 소리로 쓰면 실제 녹음 길이로, 자막에만 쓰면 자막 줄 수로 30초를 넘는지 본다
                if voice_ko:
                    if (await media_seconds(voice_ko) or 0) > MAX_SECONDS + 1:
                        raise RecordingTooLong
                elif source == "stt" and len(ai.split_script(merchant_text, limit=None)) > ai.MAX_SCRIPT_LINES:
                    raise RecordingTooLong
            if source not in ("stt", "interview"):
                voice_ko = None
            store = db.get(Store, video.store_id)
            menu = db.get(Menu, video.menu_id) if video.menu_id else None
            ctx = script_context(db, store, menu, merchant_text, langs)
            try:
                if merchant_text:
                    # 목소리로 쓰는 녹음은 말한 내용을 끝까지 자막·더빙에 담는다 (줄 수로 자르지 않는다)
                    limit = None if voice_ko else ai.MAX_SCRIPT_LINES
                    script = await asyncio.wait_for(ai.merchant_script(llm, merchant_text, langs, limit), SCRIPT_BUDGET)
                else:
                    script = await asyncio.wait_for(ai.video_script(llm, ctx, langs), SCRIPT_BUDGET)
            except TimeoutError:
                logger.warning("자막 생성 시간 초과, 대체 문장 사용 video_id=%s", video_id)
                script = (
                    ai.merchant_script_fallback(merchant_text, langs, None if voice_ko else ai.MAX_SCRIPT_LINES)
                    if merchant_text
                    else ai.fallback_script(ctx, langs)
                )
            video.progress = 20
            db.commit()

            all_langs = ["ko", *[lang for lang in langs if lang != "ko"]]
            if voice_ko:
                voices = await dub(llm, script, all_langs[1:], voice_ko, wd)
            else:
                voices = {lang: (None, "none") for lang in all_langs}
            video.progress = 35
            db.commit()

            async def timing(lang: str):
                voice, engine = voices[lang]
                # 원래 목소리에 번역 자막을 붙인 언어는 말과 글이 달라 시간을 맞출 수 없다
                if voice and (lang == "ko" or engine != "original"):
                    return await align(voice, _spoken(script[lang], lang))
                return None

            alignments = await asyncio.gather(*(timing(lang) for lang in all_langs))

            settings = get_settings()
            videos, sounds, subs = {}, {}, {}
            duration = 0.0
            for i, (lang, alignment) in enumerate(zip(all_langs, alignments)):
                voice, engine = voices[lang]
                lines = script.get(lang) or script["ko"]
                if voice:
                    spoken = await media_seconds(voice) or video_duration(len(lines))
                    length = max(spoken + TAIL_SECONDS, MIN_SECONDS)
                    cues = subtitles.build_cues(_spoken(lines, lang), lang, spoken, alignment)
                else:
                    length = video_duration(len(script["ko"]))
                    step = length / max(len(lines), 1)
                    cues = [subtitles.Cue(k * step, (k + 1) * step - 0.05, line) for k, line in enumerate(lines)]
                title, card = cards(db, store, menu, lang)
                font = settings.subtitle_font_zh if lang == "zh" else settings.subtitle_font
                ass = wd / f"sub_{lang}.ass"
                ass.write_text(subtitles.to_ass(cues, font, length, title, card), encoding="utf-8")

                rel = f"videos/{video_id}.{lang}.mp4"
                background = voices["ko"][0] if engine == "ai" else None
                await render(photos, media / rel, length, voice=voice, background=background, subtitles_ass=ass)
                vtt = f"subs/{video_id}.{lang}.vtt"
                (media / vtt).parent.mkdir(parents=True, exist_ok=True)
                (media / vtt).write_text(subtitles.to_vtt(cues), encoding="utf-8")
                videos[lang], sounds[lang], subs[lang] = rel, engine, vtt
                if lang == "ko":
                    duration = length
                video.progress = 35 + int((i + 1) / len(all_langs) * 60)
                db.commit()

            make_thumbnail(photos[0], media / "thumbs" / f"{video_id}.jpg")
            video.video_path = videos["ko"]
            video.videos, video.voices = videos, sounds
            video.thumbnail_path = f"thumbs/{video_id}.jpg"
            video.subtitles, video.script, video.duration = subs, script, duration
            video.script_source = source
            video.job_status, video.progress = "done", 100
            db.commit()
        except Exception as e:
            logger.warning("영상 생성 실패 video_id=%s: %s", video_id, type(e).__name__)
            db.rollback()
            video = db.get(Video, video_id)
            if video:
                video.job_status = "failed"
                if isinstance(e, RecordingTooLong):
                    video.error_code = "RECORDING_TOO_LONG"
                    video.error = "녹음이 30초 분량을 넘어요. 더 짧게 다시 녹음하거나 스크립트로 써 주세요."
                else:
                    video.error_code = "VIDEO_FAILED"
                    video.error = "영상 생성에 실패했어요. 다시 시도해 주세요."
                db.commit()
        finally:
            db.close()
            shutil.rmtree(wd, ignore_errors=True)


def fail_stuck_jobs() -> None:
    """서버 재시작으로 멈춘 작업을 failed 로 바꾼다."""
    with SessionLocal() as db:
        db.query(Video).filter(Video.job_status.in_(["queued", "processing"])).update(
            {
                "job_status": "failed",
                "error_code": "VIDEO_FAILED",
                "error": "서버가 재시작되어 작업이 중단됐어요. 다시 시도해 주세요.",
            }
        )
        db.commit()
