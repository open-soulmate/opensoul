"""Tests for OpenWorkflow — task automation and scheduling."""


class TestWorkflowHealth:
    def test_health(self, client):
        resp = client.get("/api/workflow/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenWorkflow"


class TestWorkflowStats:
    def test_stats(self, client):
        resp = client.get("/api/workflow/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenWorkflow"
        assert "total_tasks" in data
        assert "active_tasks" in data
        assert "by_type" in data


class TestWorkflowTasks:
    def test_list_tasks(self, client):
        resp = client.get("/api/workflow/tasks")
        # May require auth — accept 200 or 401/403
        assert resp.status_code in (200, 401, 403)

    def test_create_task_validation(self, client):
        # Missing required 'name' field should fail validation
        resp = client.post("/api/workflow/tasks", json={})
        assert resp.status_code in (400, 401, 403, 422)

    def test_create_task_with_name(self, client):
        resp = client.post(
            "/api/workflow/tasks",
            json={
                "name": "test-workflow-task",
                "description": "Created by integration test",
                "task_type": "manual",
            },
        )
        # May require auth
        assert resp.status_code in (200, 201, 401, 403)
        if resp.status_code in (200, 201):
            data = resp.json()
            assert data["name"] == "test-workflow-task"
            # Cleanup
            task_id = data.get("id")
            if task_id:
                client.delete(f"/api/workflow/tasks/{task_id}")

    def test_nonexistent_task(self, client):
        resp = client.get("/api/workflow/tasks/00000000-0000-0000-0000-000000000000")
        assert resp.status_code in (404, 401, 403, 405, 422)
