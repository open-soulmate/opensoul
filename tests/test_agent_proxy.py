"""Integration tests for Agent Proxy API (/api/agent-proxy) — agent message routing."""

import pytest


class TestAgentProxyHealth:
    def test_health(self, client):
        resp = client.get("/api/agent-proxy/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestAgentProxyAgents:
    def test_list_agents(self, client):
        resp = client.get("/api/agent-proxy/agents")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, (list, dict))


class TestAgentProxySend:
    def test_send_missing(self, client):
        resp = client.post("/api/agent-proxy/send", json={})
        assert resp.status_code in (200, 400, 401, 422)
