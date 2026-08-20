"""Integration tests for Agent API."""


class TestAgentHealth:
    def test_health(self, client):
        resp = client.get("/api/agent/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "AgentSystem"

    def test_stats(self, client):
        resp = client.get("/api/agent/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_agents" in data
        assert "by_type" in data


class TestAgentNodes:
    def test_list_nodes(self, client):
        resp = client.get("/api/agent/nodes")
        # May require auth
        assert resp.status_code in (200, 401, 403)
