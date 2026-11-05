from tests.utils import assert_error


def test_markets(client) -> None:
    markets = client.get("/api/v1/markets").json()
    names = [m["name_ko"] for m in markets]
    assert any("영천" in n for n in names) and any("포방터" in n for n in names)
    assert all(m["store_count"] >= 0 for m in markets)


def test_market_stores_pins(client) -> None:
    market = next(m for m in client.get("/api/v1/markets").json() if "영천" in m["name_ko"])
    body = client.get(f"/api/v1/markets/{market['market_id']}/stores?lang=en").json()
    assert body["stores"]
    pin = body["stores"][0]
    assert pin["directions_url"].startswith("https://www.google.com/maps/dir/?api=1&destination=")
    assert pin["price_range"]["min"] <= pin["price_range"]["max"]
    assert pin["thumbnail_url"].startswith("/media/")
    assert_error(client.get("/api/v1/markets/9999/stores"), 404, "NOT_FOUND")


def test_feed_and_media(client) -> None:
    body = client.get("/api/v1/feed?lang=en").json()
    assert len(body["items"]) >= 5
    item = body["items"][0]
    assert item["video_url"].endswith(".mp4")
    assert set(item["subtitles"]) >= {"en", "zh"}
    assert item["store"]["top_menu"]["price"] > 0
    assert client.get(item["video_url"]).status_code == 200
    vtt = client.get(item["subtitles"]["en"])
    assert vtt.status_code == 200 and vtt.text.startswith("WEBVTT")


def test_feed_cursor(client) -> None:
    first = client.get("/api/v1/feed").json()
    ids = [i["video_id"] for i in first["items"]]
    assert ids == sorted(ids, reverse=True)
    if first["next_cursor"]:
        second = client.get(f"/api/v1/feed?cursor={first['next_cursor']}").json()
        assert all(i["video_id"] < first["next_cursor"] for i in second["items"])


def test_feed_invalid_lang(client) -> None:
    assert_error(client.get("/api/v1/feed?lang=fr"), 422, "VALIDATION_ERROR")


def test_store_detail_languages(client) -> None:
    en = client.get("/api/v1/stores/1?lang=en").json()
    zh = client.get("/api/v1/stores/1?lang=zh").json()
    ko = client.get("/api/v1/stores/1?lang=ko").json()
    assert en["name"] != ko["name"] and zh["name"] != ko["name"]
    assert en["menus"][0]["description"] and en["menus"][0]["how_to_eat"]
    assert ko["menus"][0]["name"] == ko["menus"][0]["name_ko"]
    assert en["videos"]
    assert_error(client.get("/api/v1/stores/9999"), 404, "NOT_FOUND")


def test_order_preview(client) -> None:
    store = client.get("/api/v1/stores/1?lang=en").json()
    m1, m2 = store["menus"][0], store["menus"][1]
    body = client.post(
        "/api/v1/orders/preview",
        json={"store_id": 1, "items": [{"menu_id": m1["menu_id"], "qty": 2}, {"menu_id": m2["menu_id"], "qty": 1}], "lang": "en"},
    ).json()
    assert body["ko_message"] == f"{m1['name_ko']} 2개, {m2['name_ko']} 1개 주세요."
    assert body["total_price"] == m1["price"] * 2 + m2["price"]
    assert body["translated_lines"][0].startswith(m1["name"])


def test_order_preview_errors(client) -> None:
    assert_error(client.post("/api/v1/orders/preview", json={"store_id": 1, "items": []}), 422, "VALIDATION_ERROR")
    assert_error(
        client.post("/api/v1/orders/preview", json={"store_id": 1, "items": [{"menu_id": 99999, "qty": 1}]}), 404, "NOT_FOUND"
    )


def test_video_detail(client) -> None:
    vid = client.get("/api/v1/feed").json()["items"][0]["video_id"]
    body = client.get(f"/api/v1/videos/{vid}?lang=en").json()
    assert body["video_url"] and body["subtitles"]["zh"] and body["duration"]
    assert_error(client.get("/api/v1/videos/99999"), 404, "NOT_FOUND")


def test_contents(client) -> None:
    contents = client.get("/api/v1/contents?lang=en").json()
    assert [c["title_ko"] for c in contents] == ["케이팝 데몬 헌터스", "폭군의 셰프", "흑백요리사 시즌 2", "오징어 게임"]
    kdh = contents[0]
    assert kdh["title"] == "KPop Demon Hunters" and kdh["foods_ko"] == ["김밥", "라면", "국밥"]
    assert kdh["image_url"].endswith(".jpg") and client.get(kdh["image_url"]).status_code == 200

    foods = {f["food_ko"]: f for f in client.get(f"/api/v1/contents/{kdh['content_id']}/foods?lang=en").json()["foods"]}
    assert foods["김밥"]["stores"]  # 김밥 파는 시드 가게
    assert not foods["라면"]["stores"] and foods["라면"]["similar"][0]["food_ko"] == "라볶이"
    assert foods["국밥"]["similar"][0]["food_ko"] == "순대"
    assert foods["라면"]["name"].startswith("Ramyeon")
