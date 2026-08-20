"""Integration tests for OpenSessions — chat session management."""


class TestSessionsHealth:
    def test_health(self, client):
        resp = client.get("/api/sessions/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestSessionsList:
    def test_list_sessions(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200

    def test_search_sessions_unauth(self, client):
        # Search may require auth (401) — verify it at least responds
        resp = client.get("/api/sessions/search?q=test")
        assert resp.status_code in (200, 401, 403)
