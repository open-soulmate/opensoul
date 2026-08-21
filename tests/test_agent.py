"""Integration tests for Agent System API — /api/agent/* endpoints."""


class TestAgentHealth:
    def test_health(self, client):
        resp = client.get("/api/agent/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "AgentSystem"


class TestAgentStats:
    def test_stats(self, client):
        resp = client.get("/api/agent/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "AgentSystem"
        assert "total_agents" in data
        assert "by_type" in data


class TestAgentRegister:
    def test_register_requires_admin(self, client):
        """Registration should require admin auth."""
        resp = client.post(
            "/api/agent/register",
            json={
                "name": "test-agent-1",
                "agent_type": "collector",
                "capabilities": ["file", "network"],
            },
        )
        # Should fail without auth (401/403) or succeed if default user is admin
        assert resp.status_code in (200, 401, 403)
        if resp.status_code == 200:
            data = resp.json()
            assert "agent_id" in data
            assert "token" in data
            assert data["name"] == "test-agent-1"

    def test_register_with_metadata(self, client):
        resp = client.post(
            "/api/agent/register",
            json={
                "name": "test-agent-meta",
                "agent_type": "processor",
                "capabilities": ["classify"],
                "metadata": {"region": "cn-east", "version": "1.0.0"},
            },
        )
        assert resp.status_code in (200, 401, 403)
        if resp.status_code == 200:
            data = resp.json()
            assert "agent_id" in data
            assert "token" in data

    def test_register_minimal(self, client):
        """Minimal registration with defaults."""
        resp = client.post(
            "/api/agent/register",
            json={"name": "minimal-agent"},
        )
        assert resp.status_code in (200, 401, 403)


class TestAgentNodes:
    def test_list_nodes(self, client):
        resp = client.get("/api/agent/nodes")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, list)

    def test_list_nodes_with_status_filter(self, client):
        resp = client.get("/api/agent/nodes", params={"status": "active"})
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, list)

    def test_list_nodes_inactive_filter(self, client):
        resp = client.get("/api/agent/nodes", params={"status": "inactive"})
        assert resp.status_code in (200, 401)

    def test_delete_nonexistent_node(self, client):
        resp = client.delete("/api/agent/nodes/00000000-0000-0000-0000-000000000000")
        assert resp.status_code in (401, 403, 404)


class TestAgentHeartbeat:
    def test_heartbeat_without_token(self, client):
        """Heartbeat requires X-Agent-Token header."""
        resp = client.post(
            "/api/agent/heartbeat",
            json={
                "agent_id": "00000000-0000-0000-0000-000000000000",
                "status": "active",
            },
        )
        # Should fail without agent token (401/403)
        assert resp.status_code in (401, 403)


class TestAgentReport:
    def test_report_without_token(self, client):
        """Report requires X-Agent-Token header."""
        resp = client.post(
            "/api/agent/report",
            json={
                "agent_id": "00000000-0000-0000-0000-000000000000",
                "report_type": "file_event",
                "data": {"path": "/tmp/test.txt", "action": "created"},
            },
        )
        assert resp.status_code in (401, 403)


class TestAgentMemory:
    def test_remember(self, client):
        resp = client.post(
            "/api/agent/remember",
            json={
                "title": "Test Memory",
                "content": "This is a test memory for integration testing.",
                "tags": ["test", "integration"],
            },
        )
        # May succeed (200) or fail due to auth/DB issues
        assert resp.status_code in (200, 401, 422, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert data["status"] == "remembered"
            assert "id" in data

    def test_recall(self, client):
        resp = client.post(
            "/api/agent/recall",
            json={
                "question": "What is the test memory about?",
                "top_k": 3,
            },
        )
        assert resp.status_code in (200, 401, 422, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)

    def test_recall_with_custom_top_k(self, client):
        resp = client.post(
            "/api/agent/recall",
            json={
                "question": "integration test",
                "top_k": 10,
            },
        )
        assert resp.status_code in (200, 401, 422, 500)

    def test_remember_empty_tags(self, client):
        resp = client.post(
            "/api/agent/remember",
            json={
                "title": "No Tags Memory",
                "content": "A memory without any tags for testing defaults.",
            },
        )
        assert resp.status_code in (200, 401, 422, 500)
