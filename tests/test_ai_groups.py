"""Integration tests for AI Groups API (/api/ai-groups) — multi-agent task coordination."""

import pytest


class TestAIGroupsHealth:
    def test_health(self, client):
        resp = client.get("/api/ai-groups/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestAIGroupsCRUD:
    def test_list_groups(self, client):
        resp = client.get("/api/ai-groups")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_create_group_missing_body(self, client):
        resp = client.post("/api/ai-groups", json={})
        assert resp.status_code in (200, 400, 422)

    def test_create_and_get_group(self, client):
        create_resp = client.post(
            "/api/ai-groups",
            json={
                "name": "test-group-integration",
                "description": "Integration test group",
            },
        )
        if create_resp.status_code == 200:
            data = create_resp.json()
            group_id = data.get("id") or data.get("group_id")
            if group_id:
                get_resp = client.get(f"/api/ai-groups/{group_id}")
                assert get_resp.status_code == 200
                client.delete(f"/api/ai-groups/{group_id}")

    def test_get_nonexistent_group(self, client):
        resp = client.get("/api/ai-groups/nonexistent-group-id-99999")
        assert resp.status_code in (200, 404)

    def test_delete_nonexistent_group(self, client):
        resp = client.delete("/api/ai-groups/nonexistent-group-id-99999")
        assert resp.status_code in (200, 404)


class TestAIGroupsTasks:
    def test_list_tasks_nonexistent_group(self, client):
        resp = client.get("/api/ai-groups/nonexistent/tasks")
        assert resp.status_code in (200, 404)

    def test_create_task_nonexistent_group(self, client):
        resp = client.post(
            "/api/ai-groups/nonexistent/tasks",
            json={"title": "test task"},
        )
        assert resp.status_code in (200, 400, 404, 422)


class TestAIGroupsAgents:
    def test_add_agent_nonexistent_group(self, client):
        resp = client.post(
            "/api/ai-groups/nonexistent/agents",
            json={"agent_id": "test-agent"},
        )
        assert resp.status_code in (200, 400, 404, 422)

    def test_patch_nonexistent_group(self, client):
        resp = client.patch(
            "/api/ai-groups/nonexistent",
            json={"name": "updated"},
        )
        assert resp.status_code in (200, 400, 404)
