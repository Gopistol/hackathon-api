import json

import pytest

from app.llm import get_llm
from app.main import app
from app.services.video import find_span
from tests.test_video import _jpeg, _make
from tests.utils import assert_error

BASE = "/api/v1/merchant/interviews"
AUDIO = {"audio": ("blob", b"\x1aE\xdf\xa3" + b"0" * 64, "audio/webm")}


def _talk(client, iid: int, n: int) -> dict:
    out = {}
    for _ in range(n):
        out = client.post(f"{BASE}/{iid}/turns", files=AUDIO, data={"turn": str(out.get("turn", 0))}).json()
        if out["done"]:
            break
    return out


def test_register_interview_flows_into_existing_publish(client) -> None:
    start = client.post(BASE, json={"kind": "register"}).json()
    assert start["say_ko"] and start["turn"] == 0 and not start["done"]
    assert start["fillers"] == []  # 맞장구는 거슬린다는 의견이 있어 쓰지 않는다
    iid = start["interview_id"]

    # mock: 지금 묻는 주제에 맞는 고정 답 → 필수 5개를 채우면 끝
    out = _talk(client, iid, 10)
    assert out["done"] and out["progress"] == 100 and out["turn"] == 5
    draft = out["draft"]
    assert draft["store_name"] == "포방터 건어물" and draft["category"] == "건어물"
    assert [m["price"] for m in draft["items"]] == [5000, 3000]

    detail = client.get(f"{BASE}/{iid}").json()
    assert detail["status"] == "done"
    # 끝나면 기존 등록 세션(done)으로 넘어가 요약·미리보기를 그대로 쓴다
    summary = client.get(f"/api/v1/merchant/onboarding/sessions/{detail['session_id']}/summary").json()
    assert summary["done"] and summary["preview_en"]["name"]

    edited = client.patch(f"{BASE}/{iid}/draft", json={"items": [{"name_ko": "멸치", "price": 5000, "unit": "100g"}]}).json()
    assert edited["draft"]["items"][0]["unit"] == "100g" and edited["needs_check"] == []

    store_id = client.post(f"{BASE}/{iid}/publish").json()["store_id"]
    store = client.get(f"/api/v1/stores/{store_id}?lang=ko").json()
    assert store["name"] == "포방터 건어물"
    assert_error(client.post(f"{BASE}/{iid}/turns", data={"text": "또"}), 409, "INTERVIEW_DONE")


def test_stop_words_end_and_missing_items_need_check(client) -> None:
    iid = client.post(BASE, json={"kind": "register"}).json()["interview_id"]
    client.post(f"{BASE}/{iid}/turns", data={"text": "영천 반찬가게예요"})
    out = client.post(f"{BASE}/{iid}/turns", data={"text": "오늘은 그만 할게요"}).json()
    assert out["done"]
    needs = client.get(f"{BASE}/{iid}").json()["needs_check"]
    assert any("영업시간" in n for n in needs) and any("업종" in n for n in needs)


def test_turn_mismatch_and_story_needs_store(client) -> None:
    iid = client.post(BASE, json={"kind": "register"}).json()["interview_id"]
    assert_error(client.post(f"{BASE}/{iid}/turns", data={"text": "가게", "turn": "3"}), 409, "TURN_MISMATCH")
    assert_error(client.post(BASE, json={"kind": "story"}), 422, "VALIDATION_ERROR")


def test_story_interview_quotes_are_verbatim_and_make_a_video(client) -> None:
    start = client.post(BASE, json={"kind": "story", "store_id": 1}).json()
    assert "사장님" in start["say_ko"]
    iid = start["interview_id"]
    out = _talk(client, iid, 10)
    assert out["done"] and set(out["covered"]) == {"origin", "years", "craft", "people", "message"}

    detail = client.get(f"{BASE}/{iid}").json()
    turns = [t["transcript"] for t in detail["turns"]]
    assert detail["quotes"] and all(q["text"] in turns[q["turn"]] for q in detail["quotes"])
    assert client.get("/api/v1/stores/1?lang=ko").json()["story"] == detail["script_ko"]

    video = _make(client, [("photos", ("0.jpg", _jpeg("red"), "image/jpeg"))], {"interview_id": str(iid)})
    assert video["script_source"] == "interview"
    assert video["script_ko"][0] in detail["script_ko"]


class FakeLLM:
    """LLM 이 끝내자고 하거나 원문에 없는 문장을 고른 경우를 시험한다."""

    name = "fake"

    def __init__(self, replies: list[dict]) -> None:
        self.replies = replies

    async def complete(self, messages, system, max_tokens=None) -> str:
        return json.dumps(self.replies.pop(0), ensure_ascii=False)


def test_llm_cannot_finish_before_required_fields(client) -> None:
    iid = client.post(BASE, json={"kind": "register"}).json()["interview_id"]
    early = {"say": "고맙습니다", "asking": "none", "finish": True, "updates": {"store_name": "영천 떡집"}, "needs_check": []}
    # LLM 은 바뀐 항목만 보낸다. 나머지는 그대로 남아야 한다
    fix = {"say": "떡집이군요. 대표 상품이랑 가격을 알려 주세요.", "asking": "items", "finish": False,
           "updates": {"category": "떡·한과"}, "needs_check": []}  # fmt: skip
    fake = FakeLLM([early, fix])
    app.dependency_overrides[get_llm] = lambda: fake
    try:
        out = client.post(f"{BASE}/{iid}/turns", data={"text": "영천 떡집이에요"}).json()
        assert not out["done"] and out["draft"]["store_name"] == "영천 떡집"
        assert "어떤 가게" in out["say_ko"]
        out = client.post(f"{BASE}/{iid}/turns", data={"text": "떡집이에요"}).json()
    finally:
        app.dependency_overrides.pop(get_llm)
    assert out["draft"]["store_name"] == "영천 떡집" and out["draft"]["category"] == "떡·한과"
    assert out["say_ko"] == "대표 상품이랑 가격을 알려 주세요."  # "떡집이군요." 같은 되풀이는 뺀다


def _story_turns(client, store_id: int = 2) -> int:
    iid = client.post(BASE, json={"kind": "story", "store_id": store_id}).json()["interview_id"]
    for text in ["30년 전에 남편이랑 이 자리에서 시작했어요. 처음엔 꽈배기만 팔았죠.", "매일 새벽 네 시에 반죽을 해요."]:
        client.post(f"{BASE}/{iid}/turns", data={"text": text})
    return iid


def test_story_uses_only_sentences_the_merchant_said(client) -> None:
    iid = _story_turns(client)
    # 문장을 새로 쓰려 해도 받지 않는다 (ids 가 없으면 답마다 첫 문장)
    fake = FakeLLM([{"lines": ["1970년에 시작했어요.", "대통령상도 받았어요."]}])
    app.dependency_overrides[get_llm] = lambda: fake
    try:
        client.post(f"{BASE}/{iid}/finish")
    finally:
        app.dependency_overrides.pop(get_llm)
    script = client.get(f"{BASE}/{iid}").json()["script_ko"]
    assert script == "30년 전에 남편이랑 이 자리에서 시작했어요. 처음엔 꽈배기만 팔았죠. 매일 새벽 네 시에 반죽을 해요."


def test_story_picks_and_orders_whole_sentences_for_the_video(client) -> None:
    iid = _story_turns(client, store_id=1)
    # 짧은 답은 통째로 고른다: 0 "30년 전에…팔았죠." 1 "매일 새벽 네 시에 반죽을 해요."
    fake = FakeLLM([{"ids": [1, 0, 99]}])  # 없는 번호는 무시한다
    app.dependency_overrides[get_llm] = lambda: fake
    try:
        client.post(f"{BASE}/{iid}/finish")
        r = client.post(
            "/api/v1/merchant/stores/1/videos",
            files=[("photos", ("0.jpg", _jpeg("red"), "image/jpeg"))],
            data={"interview_id": str(iid)},
        )
    finally:
        app.dependency_overrides.pop(get_llm)
    detail = client.get(f"{BASE}/{iid}").json()
    assert detail["script_ko"] == "매일 새벽 네 시에 반죽을 해요. 30년 전에 남편이랑 이 자리에서 시작했어요. 처음엔 꽈배기만 팔았죠."
    job = client.get(f"/api/v1/merchant/videos/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "done", job
    video = client.get(f"/api/v1/videos/{job['video_id']}").json()
    assert video["script_source"] == "interview" and video["script_ko"][0].startswith("매일 새벽")


def test_long_story_drops_whole_sentences_not_halves() -> None:
    from app.services.interview import STORY_CHARS, _fit

    picked = [{"id": i, "turn": i, "text": f"{i}번째 문장은 이렇게 꽤 길게 이어지는 사장님의 이야기예요."} for i in range(8)]
    out = _fit(picked)
    assert sum(len(x["text"]) for x in out) <= STORY_CHARS
    assert out[0]["id"] == 0 and out[-1]["id"] == 7  # 첫 문장과 마지막 한마디는 남긴다
    assert all(x in picked for x in out)  # 문장 중간을 자르지 않는다


def test_find_span() -> None:
    chars = [(c, i * 0.1, i * 0.1 + 0.1) for i, c in enumerate("어 40년 동안")]
    assert find_span(chars, "40년 동안") == pytest.approx((0.2, 0.8))
    assert find_span(chars, "없는 말") is None


def test_video_from_thin_story_still_renders(client) -> None:
    """이야기가 거의 없고 통화가 끝나지 않았어도 영상 만들기를 누르면 어떻게든 만든다."""
    iid = client.post(BASE, json={"kind": "story", "store_id": 1}).json()["interview_id"]
    client.post(f"{BASE}/{iid}/turns", data={"text": "네"})
    client.post(f"{BASE}/{iid}/turns", data={"text": "그냥 열심히 했죠"})
    video = _make(client, [("photos", ("0.jpg", _jpeg("blue"), "image/jpeg"))], {"interview_id": str(iid)})
    detail = client.get(f"{BASE}/{iid}").json()
    assert detail["status"] == "done" and detail["quotes"] == [{"turn": 1, "text": "그냥 열심히 했죠."}]
    assert video["script_source"] == "interview"


def test_video_from_empty_story_uses_store_info(client) -> None:
    iid = client.post(BASE, json={"kind": "story", "store_id": 1}).json()["interview_id"]
    client.post(f"{BASE}/{iid}/turns", data={"text": "네"})
    video = _make(client, [("photos", ("0.jpg", _jpeg("blue"), "image/jpeg"))], {"interview_id": str(iid)})
    assert video["script_source"] == "auto"


def test_register_drops_values_the_merchant_never_said(client) -> None:
    iid = client.post(BASE, json={"kind": "register"}).json()["interview_id"]
    client.post(f"{BASE}/{iid}/turns", data={"text": "영천 떡집이에요"})
    made_up = {
        "say": "1985년부터 하셨군요. 결제는 어떻게 받으세요?", "asking": "payment", "finish": False,
        "updates": {"category": "떡·한과", "items": [{"name_ko": "꿀떡", "price": 3000}, {"name_ko": "약과", "price": 5000}],
                    "payment": ["card", "foreign_card"], "hours": "매일 09:00-20:00"},
        "needs_check": [],
    }  # fmt: skip
    fake = FakeLLM([made_up])
    app.dependency_overrides[get_llm] = lambda: fake
    try:
        out = client.post(f"{BASE}/{iid}/turns", data={"text": "꿀떡 삼천 원이고 카드 돼요"}).json()
    finally:
        app.dependency_overrides.pop(get_llm)
    d = out["draft"]
    assert [m["name_ko"] for m in d["items"]] == ["꿀떡"] and d["items"][0]["price"] == 3000  # 약과는 말한 적 없다
    assert d["payment"] == ["card"]  # 해외카드는 말하지 않았다
    assert d["hours"] is None  # 시간은 말하지 않았다
    assert "1985" not in out["say_ko"]  # 지어낸 숫자가 든 말은 고정 질문으로 바뀐다
    needs = client.get(f"{BASE}/{iid}").json()["needs_check"]
    assert any("해외카드" in n for n in needs)


def test_echo_of_ai_voice_and_stt_noise_are_asked_again(client) -> None:
    start = client.post(BASE, json={"kind": "register"}).json()
    iid = start["interview_id"]
    out = client.post(f"{BASE}/{iid}/turns", data={"text": "가게 등록을 도와드릴게요. 가게 이름이 뭐예요?"}).json()
    assert not out["heard"] and out["turn"] == 0
    assert client.get(f"{BASE}/{iid}").json()["turns"] == []


def test_browser_captions_are_used_when_server_stt_fails(client) -> None:
    iid = client.post(BASE, json={"kind": "register"}).json()["interview_id"]
    fake = FakeLLM([{"say": "영천 떡집, 좋아요. 어떤 가게예요?", "asking": "category", "finish": False,
                     "updates": {"store_name": "영천 떡집"}, "needs_check": []}])  # fmt: skip
    app.dependency_overrides[get_llm] = lambda: fake  # transcribe 가 없어서 서버 음성 인식은 실패한다
    try:
        out = client.post(f"{BASE}/{iid}/turns", files=AUDIO, data={"text": "영천 떡집이에요", "turn": "0"}).json()
    finally:
        app.dependency_overrides.pop(get_llm)
    assert out["heard"] and out["transcript"] == "영천 떡집이에요" and out["draft"]["store_name"] == "영천 떡집"


def test_items_merge_and_missing_prices_are_asked_once(client) -> None:
    iid = client.post(BASE, json={"kind": "register"}).json()["interview_id"]
    client.post(f"{BASE}/{iid}/turns", data={"text": "영천 분식이에요"})
    client.post(f"{BASE}/{iid}/turns", data={"text": "분식집이에요"})
    replies = [
        {"say": "결제는 어떻게 받으세요?", "asking": "payment", "finish": False,
         "updates": {"items": [{"name_ko": "떡볶이", "price": 4000}, {"name_ko": "순대", "price": None}]}, "needs_check": []},
        # LLM 이 이번 답의 메뉴만 보내도 떡볶이는 남아야 한다
        {"say": "결제는 어떻게 받으세요?", "asking": "payment", "finish": False,
         "updates": {"items": [{"name_ko": "어묵", "price": 1000}]}, "needs_check": []},
        {"say": "순대 가격은 얼마예요?", "asking": "items", "finish": False, "updates": {}, "needs_check": []},
    ]  # fmt: skip
    fake = FakeLLM(replies)
    app.dependency_overrides[get_llm] = lambda: fake
    try:
        out = client.post(f"{BASE}/{iid}/turns", data={"text": "떡볶이 4천 원, 순대 팔아요"}).json()
        assert out["say_ko"] == "순대 가격은 얼마예요?"  # 결제로 넘어가기 전에 빠진 가격을 한 번 묻는다
        out = client.post(f"{BASE}/{iid}/turns", data={"text": "어묵도 있어요 천 원"}).json()
        assert [m["name_ko"] for m in out["draft"]["items"]] == ["떡볶이", "순대", "어묵"]
        out = client.post(f"{BASE}/{iid}/turns", data={"text": "음 그건 그때그때 달라요"}).json()
    finally:
        app.dependency_overrides.pop(get_llm)
    assert "결제" in out["say_ko"]  # 가격은 두 번 물었으니 확인 목록으로 넘기고 다음 질문
    assert any("순대" in n for n in client.get(f"{BASE}/{iid}").json()["needs_check"])


def test_answer_is_shown_cleaned_but_raw_is_kept(client) -> None:
    iid = client.post(BASE, json={"kind": "register"}).json()["interview_id"]
    raw = "음 어 우리 가게는 그 영천 떡지비에요"
    replies = [
        {"heard": "우리 가게는 영천 떡집이에요.", "say": "어떤 가게예요?", "asking": "category", "finish": False,
         "updates": {"store_name": "영천 떡집"}, "needs_check": []},
        {"heard": "1985년부터 떡집이에요.", "say": "대표 상품이랑 가격을 알려 주세요.", "asking": "items", "finish": False,
         "updates": {"category": "떡·한과"}, "needs_check": []},
    ]  # fmt: skip
    fake = FakeLLM(replies)
    app.dependency_overrides[get_llm] = lambda: fake
    try:
        out = client.post(f"{BASE}/{iid}/turns", data={"text": raw}).json()
        assert out["transcript"] == "우리 가게는 영천 떡집이에요." and out["transcript_raw"] == raw
        out = client.post(f"{BASE}/{iid}/turns", data={"text": "떡집이에요"}).json()
    finally:
        app.dependency_overrides.pop(get_llm)
    assert out["transcript"] == "떡집이에요"  # 말하지 않은 연도가 생긴 글은 버린다
    turns = client.get(f"{BASE}/{iid}").json()["turns"]
    assert turns[0]["transcript"] == "우리 가게는 영천 떡집이에요." and turns[0]["transcript_raw"] == raw
