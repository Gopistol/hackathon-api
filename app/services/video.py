"""숏폼 생성: 사진 3~5장 + 음성 → 720x1280 MP4 + 썸네일 + 언어별 VTT.

생성형 영상 모델 없이 ffmpeg 템플릿 합성만 쓴다. 자막은 영상에 굽지 않는다.
"""

import asyncio
import io
import logging
import shutil
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import SessionLocal
from app.llm import LLMProvider
from app.models import Menu, Store, Video
from app.services import ai
from app.services.i18n import load_translations

logger = logging.getLogger(__name__)

W, H, FPS = 720, 1280, 30
TOTAL_SECONDS = 15.0
RENDER_TIMEOUT = 60
MAX_PIXELS = 48_000_000  # 8000x6000. 디코딩 메모리(RGB 약 144MB)를 서버 600MB 안에 둔다
STT_BUDGET = 10  # 초. 영상 자막용 음성 인식이 늦으면 건너뛴다
SCRIPT_BUDGET = 12  # 초. 자막 생성이 늦으면 템플릿 문장을 쓴다
ROOT = Path(__file__).resolve().parents[2]
BGM_PATH = ROOT / "demo" / "bgm.mp3"  # 있으면 음성 없는 영상에 깐다

# 메모리 600M·CPU 2개 서버라 렌더링은 한 번에 하나만
_render_lock = asyncio.Semaphore(1)


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
    """회전 보정 후 1080x1920 으로 가운데 잘라 JPEG 저장. 이미지가 아니면 AppError."""
    try:
        img = Image.open(io.BytesIO(data))  # 여기까지는 헤더만 읽는다
        if img.width * img.height > MAX_PIXELS:
            raise AppError("IMAGE_TOO_LARGE", "사진 해상도가 너무 커요. 4800만 화소 이하로 올려 주세요.", 413)
        img.draft("RGB", (1080, 1920))  # JPEG 는 필요한 크기로 줄여서 디코딩해 메모리를 아낀다
        img = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise AppError("INVALID_IMAGE", "사진 파일을 읽을 수 없어요. JPG나 PNG로 올려 주세요.", 422) from e
    img = ImageOps.fit(img, (1080, 1920), Image.Resampling.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "JPEG", quality=90)


def make_thumbnail(photo: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(photo) as img:
        img.resize((W, H), Image.Resampling.LANCZOS).save(dst, "JPEG", quality=85)


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def write_vtt(lines: list[str], duration: float, dst: Path) -> None:
    """줄마다 같은 시간씩 나눠 보여준다."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    step = duration / max(len(lines), 1)
    cues = [f"{i + 1}\n{_ts(i * step)} --> {_ts((i + 1) * step - 0.05)}\n{line}" for i, line in enumerate(lines)]
    dst.write_text("WEBVTT\n\n" + "\n\n".join(cues) + "\n", encoding="utf-8")


def _clip_filter(i: int, frames: int) -> str:
    # 짝수 장은 천천히 확대, 홀수 장은 확대한 채로 좌→우 이동
    if i % 2 == 0:
        z, x = f"1+0.12*on/{frames}", "iw/2-(iw/zoom/2)"
    else:
        z, x = "1.12", f"(iw-iw/zoom)*on/{frames}"
    return (
        f"[{i}:v]zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
        f"setsar=1,format=yuv420p[v{i}]"
    )


async def render(photos: list[Path], audio: Path | None, out: Path, on_progress=None) -> float:
    """MP4 를 만들고 길이(초)를 돌려준다. on_progress(0.0~1.0)."""
    n = len(photos)
    per = min(max(TOTAL_SECONDS / n, 3.0), 5.0)
    duration = round(per * n, 2)
    frames = int(per * FPS)

    args = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1"]
    for p in photos:
        args += ["-i", str(p)]
    if audio:
        args += ["-i", str(audio)]
    elif BGM_PATH.exists():
        args += ["-stream_loop", "-1", "-i", str(BGM_PATH)]
    else:
        args += ["-f", "lavfi", "-t", str(duration), "-i", "anullsrc=r=44100:cl=stereo"]

    clips = ";".join(_clip_filter(i, frames) for i in range(n))
    concat = "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0,fade=t=out:st={duration - 0.5}:d=0.5[v]"
    afilter = (
        f"[{n}:a]atrim=0:{duration},asetpts=PTS-STARTPTS,"
        f"afade=t=out:st={max(duration - 1, 0)}:d=1,apad=whole_dur={duration}[a]"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    args += [
        "-filter_complex", f"{clips};{concat};{afilter}",
        "-map", "[v]", "-map", "[a]", "-t", str(duration),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-threads", "2",
        str(out),
    ]  # fmt: skip

    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    async def read_progress() -> None:
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode(errors="ignore").strip()
            if line.startswith("out_time_us=") and on_progress:
                try:
                    on_progress(min(int(line.split("=", 1)[1]) / 1e6 / duration, 1.0))
                except ValueError:
                    pass

    try:
        _, stderr = await asyncio.wait_for(asyncio.gather(read_progress(), proc.stderr.read()), RENDER_TIMEOUT)
        await proc.wait()
    except TimeoutError:
        proc.kill()
        raise RuntimeError("ffmpeg 시간 초과")
    if proc.returncode != 0:
        logger.warning("ffmpeg 실패: %s", stderr.decode(errors="ignore")[-500:])
        raise RuntimeError("ffmpeg 실패")
    return duration


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
    """BackgroundTasks 로 실행. 입력은 work_dir(video_id) 에 photo_*.jpg, audio.* 로 있다."""
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
            transcript = ""
            if audio and not ai.is_mock(llm):
                try:
                    transcript = await asyncio.wait_for(
                        ai.transcribe(llm, audio.read_bytes(), audio.name, "story"), STT_BUDGET
                    )
                except (AppError, TimeoutError):
                    pass  # 자막은 가게 정보만으로도 만든다
            store = db.get(Store, video.store_id)
            menu = db.get(Menu, video.menu_id) if video.menu_id else None
            ctx = script_context(db, store, menu, transcript, langs)
            try:
                script = await asyncio.wait_for(ai.video_script(llm, ctx, langs), SCRIPT_BUDGET)
            except TimeoutError:
                logger.warning("자막 생성 시간 초과, 템플릿으로 대체 video_id=%s", video_id)
                script = ai.fallback_script(ctx, langs)
            video.progress = 20
            db.commit()

            def on_progress(ratio: float) -> None:
                video.progress = 20 + int(ratio * 70)
                db.commit()

            duration = await render(photos, audio, media / "videos" / f"{video_id}.mp4", on_progress)
            make_thumbnail(photos[0], media / "thumbs" / f"{video_id}.jpg")
            subtitles = {}
            for lang, lines in script.items():
                rel = f"subs/{video_id}.{lang}.vtt"
                write_vtt(lines, duration, media / rel)
                subtitles[lang] = rel

            video.video_path = f"videos/{video_id}.mp4"
            video.thumbnail_path = f"thumbs/{video_id}.jpg"
            video.subtitles, video.script, video.duration = subtitles, script, duration
            video.job_status, video.progress = "done", 100
            db.commit()
        except Exception as e:
            logger.warning("영상 생성 실패 video_id=%s: %s", video_id, type(e).__name__)
            db.rollback()
            video = db.get(Video, video_id)
            if video:
                video.job_status, video.error = "failed", "영상 생성에 실패했어요. 다시 시도해 주세요."
                db.commit()
        finally:
            db.close()
            shutil.rmtree(wd, ignore_errors=True)


def fail_stuck_jobs() -> None:
    """서버 재시작으로 멈춘 작업을 failed 로 바꾼다."""
    with SessionLocal() as db:
        db.query(Video).filter(Video.job_status.in_(["queued", "processing"])).update(
            {"job_status": "failed", "error": "서버가 재시작되어 작업이 중단됐어요. 다시 시도해 주세요."}
        )
        db.commit()
