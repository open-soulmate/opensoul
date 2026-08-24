"""Integration tests for Sessions API — session management."""

import pytest


class TestSessionsHealth:
    def test_health(self, client):
        resp = client.get("/api/sessions/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "SessionsAPI"


class TestSessionsList:
    def test_list_sessions(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert "total" in data
        assert isinstance(data["sessions"], list)

    def test_list_sessions_with_limit(self, client):
        resp = client.get("/api/sessions", params={"limit": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sessions"]) <= 5

    def test_list_sessions_with_offset(self, client):
        resp = client.get("/api/sessions", params={"offset": 0, "limit": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data

    def test_list_sessions_limit_boundary(self, client):
        # Limit too high — should be capped at 200
        resp = client.get("/api/sessions", params={"limit": 500})
        assert resp.status_code == 422  # FastAPI validation error

    def test_list_sessions_limit_too_low(self, client):
        resp = client.get("/api/sessions", params={"limit": 0})
        assert resp.status_code == 422

    def test_list_sessions_negative_offset(self, client):
        resp = client.get("/api/sessions", params={"offset": -1})
        assert resp.status_code == 422

    def test_list_sessions_default_params(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("limit") == 50
        assert data.get("offset") == 0


class TestSessionsSearch:
    def test_search_sessions_unauth(self, client):
        # Search may require auth (401) — verify it at least responds
        resp = client.get("/api/sessions/search", params={"q": "test"})
        assert resp.status_code in (200, 401, 403)

    def test_search_sessions_empty_query(self, client):
        resp = client.get("/api/sessions/search", params={"q": ""})
        # Empty query should return empty results or require auth
        assert resp.status_code in (200, 401, 403)

    def test_search_sessions_special_chars(self, client):
        resp = client.get("/api/sessions/search", params={"q": "'; DROP TABLE sessions;--"})
        # Should handle SQL injection safely
        assert resp.status_code in (200, 401, 403)


class TestSessionDetail:
    def test_get_session_not_found(self, client):
        resp = client.get("/api/sessions/nonexistent-session-id-12345")
        # Should return 404 or 401
        assert resp.status_code in (401, 403, 404)

    def test_delete_session_not_found(self, client):
        resp = client.delete("/api/sessions/nonexistent-session-id-12345")
        assert resp.status_code in (401, 403, 404)

    def test_get_session_messages_not_found(self, client):
        resp = client.get("/api/sessions/nonexistent-session-id-12345/messages")
        assert resp.status_code in (401, 403, 404)


class TestSessionsResponseFormat:
    def test_list_sessions_response_structure(self, client):
        resp = client.get("/api/sessions", params={"limit": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert "total" in data
        if data["sessions"]:
            session = data["sessions"][0]
            assert "id" in session
            assert "title" in session
            assert "created_at" in session
            assert "updated_at" in session
            assert "source" in session
            assert "message_count" in session
            assert "input_tokens" in session
            assert "output_tokens" in session

    def test_list_sessions_pagination_fields(self, client):
        resp = client.get("/api/sessions", params={"limit": 10, "offset": 0})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("limit") == 10
        assert data.get("offset") == 0
