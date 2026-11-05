from tests.utils import assert_error


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_path_uses_error_format(client) -> None:
    assert_error(client.get("/api/v1/nope"), 404, "NOT_FOUND")


def test_admin_reset_requires_token(client) -> None:
    assert_error(client.post("/api/v1/admin/reset"), 403, "FORBIDDEN")
    assert_error(client.post("/api/v1/admin/reset", headers={"X-Admin-Token": "wrong"}), 403, "FORBIDDEN")


def test_cors_allows_free_hosting_domains(client) -> None:
    ok = client.get("/api/v1/markets", headers={"Origin": "https://market-team-git-main.vercel.app"})
    assert ok.headers["access-control-allow-origin"] == "https://market-team-git-main.vercel.app"
    blocked = client.get("/api/v1/markets", headers={"Origin": "https://evil.vercel.app.example.com"})
    assert "access-control-allow-origin" not in blocked.headers
