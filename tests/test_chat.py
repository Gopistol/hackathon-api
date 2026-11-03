def test_ask_mock_mode(client) -> None:
    response = client.post("/ask", json={"question": "해커톤 아이디어 추천"})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "mock"
    assert body["answer"].startswith("[mock mode]")


def test_chat_mock_mode(client) -> None:
    response = client.post("/api/chat", json={"messages": [{"role": "user", "content": "안녕"}]})
    assert response.status_code == 200
    assert response.json()["mode"] == "mock"


def test_chat_stream_sse(client) -> None:
    response = client.post("/api/chat/stream", json={"messages": [{"role": "user", "content": "안녕"}]})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data: " in response.text
    assert response.text.rstrip().endswith('data: {"mode": "mock"}')


def test_chat_rejects_empty_messages(client) -> None:
    assert client.post("/api/chat", json={"messages": []}).status_code == 422


def test_files_disabled_without_s3(client) -> None:
    response = client.post("/api/files", files={"file": ("a.txt", b"hi", "text/plain")})
    assert response.status_code == 503
