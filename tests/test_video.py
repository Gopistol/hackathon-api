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

    video = client.get(f"/api/v1/videos/{job_id}").json()
    assert video["duration"] == 15.0
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
    two = [("photos", (f"{i}.jpg", _jpeg("red"), "image/jpeg")) for i in range(2)]
    assert_error(client.post("/api/v1/merchant/stores/1/videos", files=two), 422, "VALIDATION_ERROR")
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
    assert item["video_url"] == "/media/videos/showcase-yeongcheon-oil.mp4"
    assert item["duration"] == 15.0  # ffmpeg 로 읽은 길이
    assert item["thumbnail_url"] and client.get(item["thumbnail_url"]).status_code == 200  # 자동 추출
    assert set(item["subtitles"]) == {"en"}
