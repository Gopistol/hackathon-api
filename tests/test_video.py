import io

from PIL import Image

from tests.utils import assert_error


def _jpeg(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), color).save(buf, "JPEG")
    return buf.getvalue()


def test_video_job_renders_but_stays_out_of_feed(client) -> None:
    files = [("photos", (f"{i}.jpg", _jpeg(c), "image/jpeg")) for i, c in enumerate(["red", "green", "blue"])]
    r = client.post("/api/v1/merchant/stores/1/videos", files=files, data={"langs": ["en", "zh"]})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    # TestClient 는 BackgroundTasks 를 응답 직후 끝까지 실행한다.
    job = client.get(f"/api/v1/merchant/videos/jobs/{job_id}").json()
    assert job["status"] == "done", job
    assert job["progress"] == 100 and job["video_id"] == job_id
    assert job["error_code"] is None

    video = client.get(f"/api/v1/videos/{job_id}").json()
    assert video["duration"] == 20.0  # 스크립트 없음 → 가게 정보 자막 4줄 → 20초
    assert video["script_source"] == "auto"
    assert client.get(video["video_url"]).headers["content-type"] == "video/mp4"
    assert client.get(video["thumbnail_url"]).status_code == 200
    assert set(video["subtitles"]) == {"ko", "en", "zh"}
    assert "WEBVTT" in client.get(video["subtitles"]["en"]).text

    # 발표 설정(FEED_DEMO_ONLY): 생성한 영상은 피드에 안 올라가고 가게 상세에는 나온다
    assert job_id not in [i["video_id"] for i in client.get("/api/v1/feed").json()["items"]]
    assert job_id in [v["video_id"] for v in client.get("/api/v1/stores/1").json()["videos"]]


def test_photos_bracket_field_name(client) -> None:
    files = [("photos[]", (f"{i}.jpg", _jpeg("white"), "image/jpeg")) for i in range(3)]
    r = client.post("/api/v1/merchant/stores/1/videos", files=files, data={"langs[]": "en"})
    assert r.status_code == 202, r.text


def test_video_validation(client) -> None:
    six = [("photos", (f"{i}.jpg", _jpeg("red"), "image/jpeg")) for i in range(6)]
    assert_error(client.post("/api/v1/merchant/stores/1/videos", files=six), 422, "VALIDATION_ERROR")
    assert_error(client.post("/api/v1/merchant/stores/1/videos", data={"script": "안녕"}), 422, "VALIDATION_ERROR")
    two = six[:2]
    bad = [("photos", (f"{i}.jpg", b"not an image", "image/jpeg")) for i in range(3)]
    assert_error(client.post("/api/v1/merchant/stores/1/videos", files=bad), 422, "INVALID_IMAGE")
    assert_error(client.post("/api/v1/merchant/stores/9999/videos", files=two), 404, "NOT_FOUND")
    assert_error(client.get("/api/v1/merchant/videos/jobs/99999"), 404, "NOT_FOUND")


def test_showcase_video_is_seeded_into_feed(client, tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from app import seed
    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.models import Store

    demo_mp4 = Path("demo/media/videos/demo-yeongcheon-oil.mp4")
    (tmp_path / "yeongcheon-oil.mp4").write_bytes(demo_mp4.read_bytes())
    (tmp_path / "yeongcheon-oil.en.vtt").write_text("WEBVTT\n\n00:00.000 --> 00:02.000\nHello\n")
    monkeypatch.setattr(seed, "SHOWCASE", tmp_path)
    assert seed.showcase_keys() == {"yeongcheon-oil"}

    with SessionLocal() as db:
        seed._seed_showcase(db, db.get(Store, 1), "yeongcheon-oil", get_settings().media_path)
        db.commit()

    item = client.get("/api/v1/feed").json()["items"][0]
    assert item["video_url"].split("?")[0] == "/media/videos/showcase-yeongcheon-oil.mp4"
    assert item["duration"] == 15.0  # ffmpeg 로 읽은 길이
    assert item["thumbnail_url"] and client.get(item["thumbnail_url"]).status_code == 200  # 자동 추출
    assert set(item["subtitles"]) == {"en"}


def _make(client, files, data=None) -> dict:
    r = client.post("/api/v1/merchant/stores/1/videos", files=files, data=data or {})
    assert r.status_code == 202, r.text
    job = client.get(f"/api/v1/merchant/videos/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "done", job
    return client.get(f"/api/v1/videos/{job['video_id']}").json()


def _silent(client, video_url: str) -> bool:
    import subprocess
    import tempfile

    from app.services.video import ffmpeg_exe

    # Windows 는 열려 있는 임시 파일을 다른 프로세스가 못 열어서 폴더째 만든다
    with tempfile.TemporaryDirectory() as d:
        path = f"{d}/v.mp4"
        with open(path, "wb") as f:
            f.write(client.get(video_url).content)
        out = subprocess.run([ffmpeg_exe(), "-i", path, "-af", "volumedetect", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
    return "max_volume: -91" in out or "max_volume: -inf" in out


def test_one_photo_with_script_uses_script_as_subtitles(client) -> None:
    script = "40년 동안 영천시장에서 기름떡볶이만 만들어 왔어요. 바삭하게 볶아 드려요. 외국 손님도 많이 오세요. 한 번 드셔 보세요!"
    video = _make(client, [("photos", ("0.jpg", _jpeg("orange"), "image/jpeg"))], {"script": script})
    assert video["duration"] == 25.0  # 자막 5줄 x 5초
    ko = client.get(video["subtitles"]["ko"]).text
    assert "40년 동안 영천시장에서" in ko and "한 번 드셔 보세요!" in ko
    # mock 모드라 번역 대신 한국어 원문
    assert "한 번 드셔 보세요!" in client.get(video["subtitles"]["en"]).text
    assert _silent(client, video["video_url"])
    assert video["script_source"] == "script" and video["script_ko"][0] == "40년 동안 영천시장에서"


def test_unreadable_recording_becomes_script_but_not_sound(client) -> None:
    # 소리로 못 읽는 녹음이면 자막에만 쓰고 소리 없는 영상으로 만든다
    files = [("photos", ("0.jpg", _jpeg("blue"), "image/jpeg")), ("audio", ("blob", b"\x1aE\xdf\xa3" + b"0" * 64, "audio/webm"))]
    video = _make(client, files)
    # mock STT 는 고정 문장("40년 동안 영천시장에서...")을 돌려준다
    assert "40년 동안" in client.get(video["subtitles"]["ko"]).text
    assert video["script_source"] == "stt"
    assert 20.0 <= video["duration"] <= 30.0
    assert _silent(client, video["video_url"])


def _tone(seconds: float) -> bytes:
    import subprocess
    import tempfile

    from app.services.video import ffmpeg_exe

    with tempfile.TemporaryDirectory() as d:
        path = f"{d}/voice.mp3"
        subprocess.run([ffmpeg_exe(), "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", path],
                       capture_output=True, check=True)
        with open(path, "rb") as f:
            return f.read()


def test_recording_gives_voice_and_a_video_per_language(client) -> None:
    files = [("photos", (f"{i}.jpg", _jpeg(c), "image/jpeg")) for i, c in enumerate(["red", "green"])]
    files.append(("audio", ("voice.mp3", _tone(6), "audio/mpeg")))
    video = _make(client, files)
    assert video["script_source"] == "stt"
    assert video["duration"] == 20.0  # 녹음 6초 + 여유 → 최소 20초
    # 자막이 영상에 들어가서 언어마다 영상이 따로 있다. 모두 사장님 목소리(mock 은 더빙 대신 원래 목소리)
    assert set(video["video_urls"]) == {"ko", "en", "zh"}
    assert len(set(video["video_urls"].values())) == 3
    assert video["voice"] == "original"
    assert not _silent(client, video["video_urls"]["zh"])
    zh = client.get(f"/api/v1/videos/{video['video_id']}", params={"lang": "zh"}).json()
    assert zh["video_url"] == video["video_urls"]["zh"]
    assert "WEBVTT" in client.get(video["subtitles"]["zh"]).text


def test_script_length_rules(client) -> None:
    photos = [("photos", (f"{i}.jpg", _jpeg("gray"), "image/jpeg")) for i in range(2)]
    seven = " ".join(f"{i}번째 문장이에요." for i in range(1, 8))
    assert _make(client, photos, {"script": seven})["duration"] == 28.0  # 7줄 x 4초
    eight = " ".join(f"{i}번째 문장이에요." for i in range(1, 9))
    r = client.post("/api/v1/merchant/stores/1/videos", files=photos, data={"script": eight})
    assert_error(r, 422, "SCRIPT_TOO_LONG")


def test_video_duration_rule() -> None:
    from app.services.video import video_duration

    assert [video_duration(n) for n in (1, 4, 5, 6, 7)] == [20.0, 20.0, 25.0, 30.0, 28.0]


def test_plan_segments_splits_long_single_photo() -> None:
    from app.services.video import plan_segments

    segs = plan_segments(1, 25.0)
    assert len(segs) == 4 and all(i == 0 for i, _ in segs)
    assert abs(sum(sec for _, sec in segs) - 25.0) < 1e-6
    assert plan_segments(5, 20.0) == [(i, 4.0) for i in range(5)]


def test_long_recording_fails_with_clear_message(client, monkeypatch) -> None:
    from app.services import ai

    long_text = " ".join(f"{i}번째 문장이에요." for i in range(1, 10))
    monkeypatch.setitem(ai.MOCK_TRANSCRIPTS, "story", long_text)
    files = [("photos", ("0.jpg", _jpeg("red"), "image/jpeg")), ("audio", ("blob", b"\x1aE\xdf\xa3" + b"0" * 64, "audio/webm"))]
    job_id = client.post("/api/v1/merchant/stores/1/videos", files=files).json()["job_id"]
    job = client.get(f"/api/v1/merchant/videos/jobs/{job_id}").json()
    assert job["status"] == "failed" and "30초" in job["error"]
    assert job["error_code"] == "RECORDING_TOO_LONG"
    assert job["fallback_video_id"]


def test_missing_column_is_added_to_existing_table(tmp_path) -> None:
    from sqlalchemy import create_engine, inspect, text

    import app.db.session as session

    engine = create_engine(f"sqlite:///{tmp_path}/old.db")
    with engine.begin() as conn:  # script_source 가 없던 예전 videos 테이블
        conn.execute(text("CREATE TABLE videos (id INTEGER PRIMARY KEY, store_id INTEGER)"))
    original = session.engine
    session.engine = engine
    try:
        session.init_db()
    finally:
        session.engine = original
    assert "script_source" in {c["name"] for c in inspect(engine).get_columns("videos")}


def test_failed_steps_fall_back_and_video_still_renders(client, monkeypatch) -> None:
    """더빙이 터지고 첫 렌더링(전환 효과)이 실패해도 가벼운 설정으로 다시 만들어 끝낸다."""
    from app.services import video as video_service

    async def broken_dub(*args, **kwargs):
        raise KeyError("voice_id")

    real_render = video_service.render

    async def flaky_render(*args, simple=False, **kwargs):
        if not simple:
            raise RuntimeError("ffmpeg 실패")
        return await real_render(*args, simple=simple, **kwargs)

    monkeypatch.setattr(video_service, "dub", broken_dub)
    monkeypatch.setattr(video_service, "render", flaky_render)
    files = [("photos", (f"{i}.jpg", _jpeg(c), "image/jpeg")) for i, c in enumerate(["red", "green"])]
    files.append(("audio", ("voice.mp3", _tone(6), "audio/mpeg")))
    video = _make(client, files)
    assert video["script_source"] == "stt" and video["voice"] == "original"
    assert set(video["video_urls"]) == {"ko", "en", "zh"}


def test_script_video_is_narrated_when_a_voice_is_available(client, monkeypatch) -> None:
    """녹음 없이 스크립트만 써도 시장 할머니 목소리로 언어마다 읽는다."""
    from app.services import video as video_service

    async def fake_speak(llm, text, lang, voice_id):
        return _tone(3), "clone"

    monkeypatch.setattr(video_service, "_speak", fake_speak)
    files = [("photos", ("0.jpg", _jpeg("red"), "image/jpeg"))]
    video = _make(client, files, {"script": "떡볶이 한 접시 4천 원이에요. 맛있게 드세요."})
    assert video["script_source"] == "script" and video["voice"] == "narrator"
    assert not _silent(client, video["video_urls"]["en"])
