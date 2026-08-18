"""Integration tests for OpenNerve (神经) — event bus, WebSocket."""


class TestNerveHealth:
    def test_health(self, client):
        resp = client.get("/api/nerve/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
