import io

from PIL import Image

from tests.utils import assert_error


def _jpeg(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), color).save(buf, "JPEG")
    return buf.getvalue()


def test_video_job_renders_and_shows_in_feed(client) -> None:
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

    assert client.get("/api/v1/feed").json()["items"][0]["video_id"] == job_id


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
