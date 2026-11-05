from app.services import ai
from tests.utils import assert_error

BASE = "/api/v1/merchant/onboarding/sessions"


def _run_all_steps(client, sid: int) -> None:
    for step in ai.STEPS:
        r = client.post(f"{BASE}/{sid}/answers", files={"audio": ("a.webm", b"fake-audio", "audio/webm")})
        assert r.status_code == 200, r.text
        assert r.json()["step"] == step
        client.post(f"{BASE}/{sid}/confirm", json={"step": step, "accepted": True})


def test_full_onboarding_flow_appears_in_store_pins(client) -> None:
    start = client.post(BASE, json={}).json()
    assert start["step"] == "store_name" and start["question_ko"]
    sid = start["session_id"]

    r = client.post(f"{BASE}/{sid}/answers", data={"text": "포방터 순대집이에요"}).json()
    assert r["parsed"]["name_ko"] == "포방터 순대집"
    assert "포방터 순대집" in r["confirm_text_ko"]

    # '다시' → 같은 질문
    again = client.post(f"{BASE}/{sid}/confirm", json={"step": "store_name", "accepted": False}).json()
    assert again["retry"] and again["step"] == "store_name"
    nxt = client.post(f"{BASE}/{sid}/confirm", json={"step": "store_name", "accepted": True}).json()
    assert nxt["step"] == "menus"

    r = client.post(f"{BASE}/{sid}/answers", data={"text": "순대 5천원, 김밥 3000원이요"}).json()
    assert [m["price"] for m in r["parsed"]["menus"]] == [5000, 3000]
    client.post(f"{BASE}/{sid}/confirm", json={"step": "menus", "accepted": True})

    r = client.post(f"{BASE}/{sid}/answers", data={"text": "현금이랑 카드 돼요"}).json()
    assert "해외카드 가능 여부" in r["needs_check"]
    # 사장님이 직접 고침
    client.post(f"{BASE}/{sid}/confirm", json={"step": "payment", "accepted": True, "edits": {"payment": ["cash", "card", "foreign_card"]}})

    for step, text in [("hours", "아침 10시부터 밤 9시까지"), ("story", "30년 동안 순대만 만들었어요")]:
        client.post(f"{BASE}/{sid}/answers", data={"text": text})
        last = client.post(f"{BASE}/{sid}/confirm", json={"step": step, "accepted": True}).json()
    assert last["done"] is True

    summary = client.get(f"{BASE}/{sid}/summary").json()
    assert summary["steps"]["payment"]["parsed"]["payment"] == ["cash", "card", "foreign_card"]
    assert summary["preview_en"]["menus"][0]["name"].startswith("Sundae")

    store_id = client.post(f"{BASE}/{sid}/publish").json()["store_id"]
    # 두 번 올려도 같은 가게
    assert client.post(f"{BASE}/{sid}/publish").json()["store_id"] == store_id

    detail = client.get(f"/api/v1/stores/{store_id}?lang=en").json()
    assert detail["name_ko"] == "포방터 순대집"
    assert detail["hours"] == "10:00-21:00"
    assert detail["menus"][0]["how_to_eat"]


def test_summary_has_chinese_preview(client) -> None:
    sid = client.post(BASE).json()["session_id"]
    _run_all_steps(client, sid)
    summary = client.get(f"{BASE}/{sid}/summary").json()
    zh = summary["preview_zh"]
    assert zh is not None and "translated" in zh
    assert set(zh) == set(summary["preview_en"])


def test_audio_answers_in_mock_mode(client) -> None:
    sid = client.post(BASE).json()["session_id"]
    _run_all_steps(client, sid)
    assert client.get(f"{BASE}/{sid}/summary").json()["done"]
    assert client.post(f"{BASE}/{sid}/publish").json()["store_id"]


def test_onboarding_errors(client) -> None:
    sid = client.post(BASE, json={}).json()["session_id"]
    assert_error(client.post(f"{BASE}/{sid}/answers"), 422, "VALIDATION_ERROR")
    assert_error(client.post(f"{BASE}/{sid}/confirm", json={"step": "menus", "accepted": True}), 409, "STEP_MISMATCH")
    assert_error(client.post(f"{BASE}/{sid}/confirm", json={"step": "store_name", "accepted": True}), 409, "NO_ANSWER")
    assert_error(client.post(f"{BASE}/{sid}/publish"), 409, "ONBOARDING_INCOMPLETE")
    assert_error(client.post(f"{BASE}/99999/answers", data={"text": "x"}), 404, "NOT_FOUND")
    assert_error(client.post(BASE, json={"store_id": 99999}), 404, "NOT_FOUND")


def test_completed_registration_payment_can_be_edited_and_published(client) -> None:
    sid = client.post(BASE, json={}).json()["session_id"]
    _run_all_steps(client, sid)
    before = client.get(f"{BASE}/{sid}/summary").json()
    payment = ["cash", "card", "foreign_card", "onnuri"]

    result = client.post(
        f"{BASE}/{sid}/confirm",
        json={"step": "payment", "accepted": True, "edits": {"payment": payment}},
    )
    assert result.status_code == 200, result.text
    assert result.json()["done"] is True
    assert result.json()["step"] == "done"

    after = client.get(f"{BASE}/{sid}/summary").json()
    assert after["steps"]["payment"]["parsed"]["payment"] == payment
    assert after["steps"]["payment"]["needs_check"] == []
    assert after["steps"]["payment"]["transcript"] == before["steps"]["payment"]["transcript"]
    for step in ai.STEPS:
        if step != "payment":
            assert after["steps"][step] == before["steps"][step]
    for lang in ("en", "zh"):
        assert after[f"preview_{lang}"]["payment"] == payment

    assert_error(client.post(f"{BASE}/{sid}/confirm", json={"step": "payment", "accepted": False}), 409, "STEP_MISMATCH")
    store_id = client.post(f"{BASE}/{sid}/publish").json()["store_id"]
    detail = client.get(f"/api/v1/stores/{store_id}?lang=ko").json()
    assert detail["payment"] == payment
