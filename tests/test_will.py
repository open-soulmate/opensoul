"""Integration tests for OpenWill (意志) — workflow engine."""


class TestWillHealth:
    def test_health(self, client):
        resp = client.get("/api/will/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestWillWorkflows:
    def test_list_workflows(self, client):
        resp = client.get("/api/will/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_create_workflow(self, client):
        resp = client.post(
            "/api/will/workflows",
            json={
                "name": "Test Workflow",
                "description": "A test workflow",
                "nodes": [],
                "edges": [],
            },
        )
        assert resp.status_code in (200, 201)

    def test_create_and_get_workflow(self, client):
        # Create
        resp = client.post(
            "/api/will/workflows",
            json={
                "name": "Get Test WF",
                "description": "Test",
                "nodes": [],
                "edges": [],
            },
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            wf_id = data.get("workflow_id") or data.get("id")
            if wf_id:
                resp2 = client.get(f"/api/will/workflows/{wf_id}")
                assert resp2.status_code == 200

    def test_validate_workflow(self, client):
        # Create first
        resp = client.post(
            "/api/will/workflows",
            json={
                "name": "Validate Test",
                "description": "Test",
                "nodes": [],
                "edges": [],
            },
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            wf_id = data.get("workflow_id") or data.get("id")
            if wf_id:
                resp2 = client.get(f"/api/will/workflows/{wf_id}/validate")
                assert resp2.status_code == 200


class TestWillExecutions:
    def test_list_executions(self, client):
        resp = client.get("/api/will/executions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))
