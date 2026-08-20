"""Integration tests for OpenSoul Notification Center API."""


class TestNotificationsHealth:
    def test_health_returns_200(self, client):
        resp = client.get("/api/notifications/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestNotificationsRecent:
    def test_recent_returns_notifications(self, client):
        resp = client.get("/api/notifications/recent")
        assert resp.status_code == 200
        data = resp.json()
        assert "notifications" in data
        assert isinstance(data["notifications"], list)
        assert "total" in data
        assert "unread_count" in data

    def test_recent_with_limit(self, client):
        resp = client.get("/api/notifications/recent", params={"limit": 3})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["notifications"]) <= 3


class TestNotificationsUnreadCount:
    def test_unread_count_returns_200(self, client):
        resp = client.get("/api/notifications/unread-count")
        assert resp.status_code == 200


class TestNotificationsReadAll:
    def test_read_all_returns_200(self, client):
        resp = client.post("/api/notifications/read-all")
        assert resp.status_code == 200


class TestNotificationsStats:
    def test_stats_returns_200(self, client):
        resp = client.get("/api/notifications/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestNotificationsForwardRules:
    def test_forward_rules_returns_200(self, client):
        resp = client.get("/api/notifications/forward/rules")
        assert resp.status_code == 200
