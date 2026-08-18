"""Integration tests for OpenMirror (镜像) — sandbox testing."""


class TestMirrorHealth:
    def test_health(self, client):
        resp = client.get("/api/mirror/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenMirror"


class TestMirrorSandbox:
    def test_create_list_delete_sandbox(self, client):
        # Create
        resp = client.post(
            "/api/mirror/sandboxes",
            json={
                "name": "test_sandbox",
                "description": "Integration test sandbox",
                "config": {"isolated": True},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        sid = data["sandbox_id"]

        # List
        resp = client.get("/api/mirror/sandboxes")
        assert resp.status_code == 200

        # Get specific
        resp = client.get(f"/api/mirror/sandboxes/{sid}")
        assert resp.status_code == 200

        # Delete
        resp = client.delete(f"/api/mirror/sandboxes/{sid}")
        assert resp.status_code == 200

    def test_sandbox_variables(self, client):
        # Create sandbox
        resp = client.post(
            "/api/mirror/sandboxes",
            json={
                "name": "var_test",
                "config": {},
            },
        )
        assert resp.status_code == 200
        sid = resp.json()["sandbox_id"]

        # Set variable
        resp = client.post(
            f"/api/mirror/sandboxes/{sid}/variables",
            json={
                "key": "test_var",
                "value": "hello",
            },
        )
        assert resp.status_code == 200

        # Get variables
        resp = client.get(f"/api/mirror/sandboxes/{sid}/variables")
        assert resp.status_code == 200

        # Cleanup
        client.delete(f"/api/mirror/sandboxes/{sid}")


class TestMirrorCleanup:
    def test_cleanup(self, client):
        resp = client.post("/api/mirror/cleanup")
        assert resp.status_code == 200
