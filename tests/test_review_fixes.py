"""코드 리뷰에서 나온 문제들이 다시 생기지 않게 막는 테스트."""

import asyncio
import io
import json
import logging
from types import SimpleNamespace

from PIL import Image

from app.core.config import Settings
from app.services import ai, video
from tests.utils import assert_error


def _jpeg(color="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), color).save(buf, "JPEG")
    return buf.getvalue()


class _JsonLLM:
    name = "openai"

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def complete(self, messages, system, max_tokens=None):
        return json.dumps(self.payload)


def test_translation_with_wrong_structure_keeps_fallback() -> None:
    menus = [{"id": "m0", "name_ko": "순대", "price": 5000}]
    for bad in ({"en": {"menus": 123}}, {"en": {"menus": [{"id": ["m0"], "name": "x"}]}}, {"en": "oops"}):
        result = asyncio.run(ai.translate_store(_JsonLLM(bad), "가게", "소개", menus, ("en",)))
        assert result["en"]["menus"]["m0"]["name"].startswith("Sundae")
        assert result["en"]["store"]["name"] == "가게"


def test_httpx_request_logs_are_off() -> None:
    # Geocoding 키가 쿼리에 들어가므로 httpx INFO 로그(요청 URL)를 남기지 않는다
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_upload_over_limit_is_rejected(client) -> None:
    files = [("photos", (f"{i}.jpg", _jpeg(), "image/jpeg")) for i in range(2)]
    files.append(("photos", ("big.jpg", b"\0" * (10 * 1024 * 1024 + 1), "image/jpeg")))
    assert_error(client.post("/api/v1/merchant/stores/1/videos", files=files), 413, "UPLOAD_TOO_LARGE")


def test_huge_resolution_rejected_and_work_dir_cleaned(client) -> None:
    buf = io.BytesIO()
    Image.new("1", (9000, 6000)).save(buf, "PNG")  # 파일은 작지만 5400만 화소
    files = [("photos", ("0.jpg", _jpeg(), "image/jpeg")), ("photos", ("1.jpg", _jpeg(), "image/jpeg"))]
    files.append(("photos", ("huge.png", buf.getvalue(), "image/png")))
    assert_error(client.post("/api/v1/merchant/stores/1/videos", files=files), 413, "IMAGE_TOO_LARGE")
    work_root = video.work_dir(0).parent
    assert not work_root.exists() or not any(work_root.iterdir())


def test_failed_job_points_to_fallback_video(client, monkeypatch) -> None:
    async def broken_render(*args, **kwargs):
        raise RuntimeError("ffmpeg 실패")

    monkeypatch.setattr(video, "render", broken_render)
    files = [("photos", (f"{i}.jpg", _jpeg(), "image/jpeg")) for i in range(3)]
    job_id = client.post("/api/v1/merchant/stores/1/videos", files=files).json()["job_id"]
    job = client.get(f"/api/v1/merchant/videos/jobs/{job_id}").json()
    assert job["status"] == "failed" and job["error"]
    assert job["error_code"] == "VIDEO_FAILED"
    fallback = client.get(f"/api/v1/videos/{job['fallback_video_id']}").json()
    assert fallback["store_id"] == 1


def test_slow_video_script_falls_back_to_template(monkeypatch) -> None:
    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(video, "SCRIPT_BUDGET", 0.05)
    monkeypatch.setattr(ai, "video_script", slow)
    ctx = {"store_name": "가게", "market_name": "시장", "menu_name": "순대", "price": 5000, "story": "", "i18n": {}}

    async def run():
        try:
            return await asyncio.wait_for(ai.video_script(None, ctx, ["en"]), video.SCRIPT_BUDGET)
        except TimeoutError:
            return ai.fallback_script(ctx, ["en"])

    assert asyncio.run(run())["ko"][0] == "가게 · 시장"


def test_openai_timeout_default_is_short() -> None:
    fields = Settings.model_fields
    assert fields["openai_timeout"].default <= 15 and fields["openai_max_retries"].default == 0


def test_payment_negations() -> None:
    cases = {
        "카드는 안 돼요. 현금만 받고 온누리상품권은 안 받아요": ["cash"],
        "현금이랑 카드 다 돼요. 온누리상품권도 받아요": ["cash", "card", "onnuri"],
        "현금만 돼요 해외카드는 안 돼요": ["cash"],
        "카드는 되는데 해외카드는 안 돼요": ["card"],
        "카드 단말기가 없어서 현금만 받아요": ["cash"],
    }
    for text, expected in cases.items():
        assert ai._rule_payment(text)[0]["payment"] == expected, text


def test_llm_payment_contradicting_merchant_is_removed() -> None:
    llm = _JsonLLM({"payment": ["cash", "card"], "needs_check": []})
    parsed, needs = asyncio.run(ai.structure_step(llm, "payment", "카드는 안 돼요. 현금만 받아요"))
    assert parsed["payment"] == ["cash"]
    assert "카드 가능 여부" in needs


def test_order_with_unpriced_menu_is_blocked(client) -> None:
    store = client.get("/api/v1/stores/5?lang=ko").json()
    menu_id = store["menus"][-1]["menu_id"]
    assert client.patch(f"/api/v1/merchant/menus/{menu_id}", json={"price": None}).status_code == 200
    r = client.post("/api/v1/orders/preview", json={"store_id": 5, "items": [{"menu_id": menu_id, "qty": 2}]})
    assert_error(r, 409, "PRICE_UNKNOWN")


def test_patch_rejects_explicit_null_for_required_fields(client) -> None:
    menu_id = client.get("/api/v1/stores/2").json()["menus"][0]["menu_id"]
    assert_error(client.patch(f"/api/v1/merchant/menus/{menu_id}", json={"name_ko": None}), 422, "VALIDATION_ERROR")
    assert_error(client.patch("/api/v1/merchant/stores/2", json={"name": None}), 422, "VALIDATION_ERROR")
    assert_error(client.patch("/api/v1/merchant/stores/2", json={"payment": None}), 422, "VALIDATION_ERROR")
    # 생략한 필드는 그대로, address 는 null 허용
    assert client.patch("/api/v1/merchant/stores/2", json={"address": None}).status_code == 200
    assert client.patch(f"/api/v1/merchant/menus/{menu_id}", json={"price": 1500}).status_code == 200


def test_store_name_starting_with_particle_like_syllable() -> None:
    assert ai._rule_store_name("은혜분식이에요")[0]["name_ko"] == "은혜분식"
    assert ai._rule_store_name("저희 가게는 은혜분식이에요")[0]["name_ko"] == "은혜분식"
    assert ai._rule_store_name("이름은 포방터 떡볶이요")[0]["name_ko"] == "포방터 떡볶이"


def test_hours_keep_minutes() -> None:
    assert ai._rule_hours("오전 10시 30분부터 오후 8시 30분까지 영업해요") == ({"hours": "10:30-20:30"}, [])
    assert ai._rule_hours("아침 9시 반부터 저녁 9시까지")[0]["hours"] == "09:30-21:00"
    # 규칙이 못 읽는 분 표현은 LLM 으로 넘어가도록 확인 요청을 남긴다
    assert ai._rule_hours("10시 삼십 분부터 9시까지")[1]
