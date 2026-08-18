"""Integration tests for OpenLimb (四肢) — RPA task executor."""


class TestLimbHealth:
    def test_health(self, client):
        resp = client.get("/api/limb/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenLimb"


class TestLimbTasks:
    def test_create_list_delete_task(self, client):
        resp = client.post(
            "/api/limb/tasks",
            json={
                "name": "test_task",
                "task_type": "http_request",
                "actions": [
                    {
                        "type": "http_request",
                        "config": {
                            "method": "GET",
                            "url": "https://httpbin.org/get",
                        },
                    }
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        tid = data["task_id"]

        resp = client.get("/api/limb/tasks")
        assert resp.status_code == 200

        resp = client.get(f"/api/limb/tasks/{tid}")
        assert resp.status_code == 200

        # Delete
        resp = client.delete(f"/api/limb/tasks/{tid}")
        assert resp.status_code == 200


class TestLimbTemplates:
    def test_list_templates(self, client):
        resp = client.get("/api/limb/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list) or "templates" in data


class TestLimbStats:
    def test_stats(self, client):
        resp = client.get("/api/limb/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_tasks" in data

    def test_history(self, client):
        resp = client.get("/api/limb/history")
        assert resp.status_code == 200
