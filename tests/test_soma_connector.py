"""Tests for OpenSoma Connector API — component registration, heartbeat, and data push."""


class TestSomaHealth:
    def test_soma_health(self, client):
        resp = client.get("/api/soma/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestComponentRegistration:
    def test_register_component(self, client):
        resp = client.post("/api/soma/register", json={
            "component_id": "test-node-1",
            "name": "Test Node",
            "component_type": "collector",
            "version": "0.1.0",
            "capabilities": ["file", "clipboard"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["component_id"] == "test-node-1"
        assert data["name"] == "Test Node"
        assert data["status"] == "online"
        assert "secret_token" in data

    def test_register_updates_existing(self, client):
        # Register again with different version
        resp = client.post("/api/soma/register", json={
            "component_id": "test-node-1",
            "name": "Test Node Updated",
            "component_type": "collector",
            "version": "0.2.0",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Node Updated"

    def test_list_components(self, client):
        resp = client.get("/api/soma/components")
        assert resp.status_code == 200
        data = resp.json()
        assert "components" in data
        ids = [c["component_id"] for c in data["components"]]
        assert "test-node-1" in ids

    def test_get_component(self, client):
        resp = client.get("/api/soma/components/test-node-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["component_id"] == "test-node-1"
        assert data["component_type"] == "collector"

    def test_get_nonexistent_component(self, client):
        resp = client.get("/api/soma/components/nonexistent-xyz-999")
        assert resp.status_code == 404


class TestHeartbeat:
    def test_heartbeat(self, client):
        # First register to get token
        reg = client.post("/api/soma/register", json={
            "component_id": "test-hb-node",
            "name": "HB Test",
            "component_type": "test",
            "version": "0.0.1",
        })
        token = reg.json().get("secret_token", "")

        resp = client.post("/api/soma/heartbeat",
            json={"component_id": "test-hb-node"},
            headers={"X-Component-Token": token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["component_id"] == "test-hb-node"
        assert data["status"] == "acknowledged"


class TestDataPush:
    def test_push_data(self, client):
        # Register to get token
        reg = client.post("/api/soma/register", json={
            "component_id": "test-push-node",
            "name": "Push Test",
            "component_type": "test",
            "version": "0.0.1",
        })
        token = reg.json().get("secret_token", "")

        resp = client.post("/api/soma/push",
            json={
                "data_type": "file_change",
                "payload": {"path": "/tmp/test.txt", "action": "created"},
            },
            headers={
                "X-Component-Id": "test-push-node",
                "X-Component-Token": token,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data_type"] == "file_change"

    def test_push_data_missing_header(self, client):
        resp = client.post("/api/soma/push", json={
            "data_type": "test",
            "payload": {},
        })
        assert resp.status_code == 400


class TestCapabilities:
    def test_capabilities(self, client):
        resp = client.get("/api/soma/capabilities")
        assert resp.status_code == 200


class TestStats:
    def test_stats(self, client):
        resp = client.get("/api/soma/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data or "total_components" in data


class TestComponentCleanup:
    def test_delete_component(self, client):
        # Register a temp component
        client.post("/api/soma/register", json={
            "component_id": "test-delete-me",
            "name": "Delete Me",
            "component_type": "test",
            "version": "0.0.1",
        })
        # Delete it
        resp = client.delete("/api/soma/components/test-delete-me")
        assert resp.status_code == 200

        # Verify it's gone
        resp = client.get("/api/soma/components/test-delete-me")
        assert resp.status_code == 404
