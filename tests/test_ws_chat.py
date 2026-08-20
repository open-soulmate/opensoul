"""Integration tests for WebSocket Chat API."""


class TestWsChatHealth:
    def test_health(self, client):
        resp = client.get("/api/ws/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
