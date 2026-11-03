def test_item_crud(client) -> None:
    created = client.post("/api/items", json={"title": "hello"})
    assert created.status_code == 201
    item_id = created.json()["id"]

    assert client.get(f"/api/items/{item_id}").json()["title"] == "hello"
    assert any(i["id"] == item_id for i in client.get("/api/items").json())

    assert client.delete(f"/api/items/{item_id}").status_code == 204
    assert client.get(f"/api/items/{item_id}").status_code == 404
