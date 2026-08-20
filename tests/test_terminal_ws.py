"""Integration tests for Terminal WebSocket API."""


class TestTerminalHealth:
    def test_health(self, client):
        resp = client.get("/api/terminal/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
