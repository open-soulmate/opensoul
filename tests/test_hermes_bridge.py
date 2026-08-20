"""Integration tests for Hermes Bridge API (/api/hermes) — sessions, messages."""

import pytest


class TestHermesBridgeHealth:
    def test_health(self, client):
        resp = client.get("/api/hermes/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestHermesBridgeSessions:
    def test_list_sessions(self, client):
        resp = client.get("/api/hermes/sessions")
        assert resp.status_code in (200, 401)

    def test_list_sessions_with_limit(self, client):
        resp = client.get("/api/hermes/sessions?limit=5")
        assert resp.status_code in (200, 401)

    def test_get_session_messages_nonexistent(self, client):
        resp = client.get("/api/hermes/sessions/nonexistent-session/messages")
        assert resp.status_code in (200, 401, 404)

    def test_delete_session_nonexistent(self, client):
        resp = client.delete("/api/hermes/sessions/nonexistent-session")
        assert resp.status_code in (200, 401, 404)

    def test_send_message_missing(self, client):
        resp = client.post("/api/hermes/sessions/send", json={})
        assert resp.status_code in (200, 400, 401, 422)

    def test_search_sessions(self, client):
        resp = client.get("/api/hermes/sessions/search?q=test")
        assert resp.status_code in (200, 401)
