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
    assert item["video_url"].split("?")[0].endswith(".mp4") and "?v=" in item["video_url"]  # 교체 시 캐시 무효화
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
    assert kdh["image_url"].split("?")[0].endswith(".jpg") and client.get(kdh["image_url"]).status_code == 200

    foods = {f["food_ko"]: f for f in client.get(f"/api/v1/contents/{kdh['content_id']}/foods?lang=en").json()["foods"]}
    assert foods["김밥"]["stores"]  # 김밥 파는 시드 가게
    assert not foods["라면"]["stores"] and foods["라면"]["similar"][0]["food_ko"] == "라볶이"
    assert foods["국밥"]["stores"]  # 순대국밥 파는 시드 가게로 바로 연결
    assert foods["라면"]["name"].startswith("Ramyeon")
    for f in [*foods.values(), foods["라면"]["similar"][0]]:  # 음식·비슷한 음식 모두 사진이 있다
        assert client.get(f["image_url"]).status_code == 200

    by_title = {c["title_ko"]: c["content_id"] for c in contents}
    for title, food in [("폭군의 셰프", "비빔밥"), ("흑백요리사 시즌 2", "나물")]:
        f = client.get(f"/api/v1/contents/{by_title[title]}/foods?lang=ko").json()["foods"][0]
        assert f["food_ko"] == food and f["stores"][0]["name"] == "영천 나물 비빔밥"


def test_content_only_foods_do_not_change_menu_translation() -> None:
    from app.services.i18n import food_name, glossary_entry

    assert glossary_entry("짜장라면") is None  # 작품용 "라면"이 메뉴 맵기·번역에 섞이지 않음
    assert food_name("라면", "en").startswith("Ramyeon")
    assert glossary_entry("순대국밥")["en"][0].startswith("Sundae Gukbap")


def test_seed_menu_and_store_photos(client) -> None:
    for store_id in range(1, 7):
        ko = client.get(f"/api/v1/stores/{store_id}?lang=ko").json()
        en = client.get(f"/api/v1/stores/{store_id}?lang=en").json()
        assert ko["thumbnail_url"]
        assert client.get(ko["thumbnail_url"]).status_code == 200
        assert [m["image_url"] for m in ko["menus"]] == [m["image_url"] for m in en["menus"]]
        for menu in ko["menus"]:
            assert menu["image_url"].startswith("/media/foods/")
            assert "?v=" in menu["image_url"]
            image = client.get(menu["image_url"])
            assert image.status_code == 200
            assert image.headers["content-type"].startswith("image/")


def test_market_photos_and_video_less_store(client) -> None:
    markets = client.get("/api/v1/markets").json()
    for market in markets:
        if not market["store_count"]:
            continue
        assert market["thumbnail_url"]
        assert client.get(market["thumbnail_url"]).status_code == 200
        pins = client.get(f"/api/v1/markets/{market['market_id']}/stores").json()
        assert pins["market"]["thumbnail_url"] == market["thumbnail_url"]
        assert all(pin["thumbnail_url"] and pin["top_menu"]["image_url"] for pin in pins["stores"])
    # 마지막 시드 가게는 테스트용 영상이 없어도 대표 메뉴 사진이 있다.
    detail = client.get("/api/v1/stores/6").json()
    assert not detail["videos"]
    assert "bibimbap.jpg?v=" in detail["thumbnail_url"]


def test_menu_photo_does_not_mismatch_unknown_names(client) -> None:
    from app.services.views import menu_photo

    assert menu_photo("순대국밥").split("?")[0].endswith("gukbap.jpg")
    assert menu_photo("나물 반찬").split("?")[0].endswith("namul.jpg")
    assert menu_photo("순대국밥 신메뉴") is None
    assert menu_photo("없는 메뉴") is None
