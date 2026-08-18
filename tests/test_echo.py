"""Integration tests for OpenEcho (回声) — message dispatch."""


class TestEchoHealth:
    def test_health(self, client):
        resp = client.get("/api/echo/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenEcho"


class TestEchoChannels:
    def test_list_channels(self, client):
        resp = client.get("/api/echo/channels")
        assert resp.status_code == 200
        channels = resp.json()
        assert isinstance(channels, list) or "channels" in channels

    def test_configure_channel(self, client):
        resp = client.post(
            "/api/echo/channels/configure",
            json={
                "channel": "console",
                "enabled": True,
            },
        )
        assert resp.status_code == 200


class TestEchoSend:
    def test_send_console(self, client):
        resp = client.post(
            "/api/echo/send",
            json={
                "channel": "console",
                "title": "Test Message",
                "content": "Integration test from OpenSoul",
                "priority": 5,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_broadcast(self, client):
        resp = client.post(
            "/api/echo/broadcast",
            json={
                "title": "Broadcast Test",
                "content": "Testing broadcast",
                "priority": 5,
            },
        )
        assert resp.status_code == 200


class TestEchoHistory:
    def test_history(self, client):
        resp = client.get("/api/echo/history")
        assert resp.status_code == 200
