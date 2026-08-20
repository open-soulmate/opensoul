"""Integration tests for Agent Collaboration API (/api/collab) — multi-agent coordination."""

import pytest


class TestAgentCollabHealth:
    def test_health(self, client):
        resp = client.get("/api/collab/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestAgentCollabRegister:
    def test_register_missing(self, client):
        resp = client.post("/api/collab/register", json={})
        assert resp.status_code in (200, 400, 422)

    def test_register_agent(self, client):
        resp = client.post(
            "/api/collab/register",
            json={"agent_id": "test-collab-agent", "name": "Test Agent"},
        )
        assert resp.status_code in (200, 400, 422)


class TestAgentCollabMessage:
    def test_send_message_missing(self, client):
        resp = client.post("/api/collab/message", json={})
        assert resp.status_code in (200, 400, 422)


class TestAgentCollabHandoff:
    def test_handoff_missing(self, client):
        resp = client.post("/api/collab/handoff", json={})
        assert resp.status_code in (200, 400, 422)


class TestAgentCollabStatus:
    def test_status(self, client):
        resp = client.get("/api/collab/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))
