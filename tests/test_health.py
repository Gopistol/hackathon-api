def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_page(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Hackathon Demo" in response.text
