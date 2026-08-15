"""Integration tests for OpenLink (突触) — external system connectors."""

import pytest


class TestLinkHealth:
    def test_health(self, client):
        resp = client.get("/api/link/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenLink"


class TestLinkConnectors:
    def test_create_list_delete_connector(self, client):
        # Create
        resp = client.post("/api/link/connectors", json={
            "name": "test_connector",
            "type": "webhook_in",
            "endpoint": "https://example.com/hook",
            "description": "Integration test",
        })
        assert resp.status_code == 200
        data = resp.json()
        cid = data["connector_id"]

        # List
        resp = client.get("/api/link/connectors")
        assert resp.status_code == 200

        # Get specific
        resp = client.get(f"/api/link/connectors/{cid}")
        assert resp.status_code == 200

        # Update
        resp = client.patch(f"/api/link/connectors/{cid}", json={
            "description": "Updated by test",
        })
        assert resp.status_code == 200

        # Delete
        resp = client.delete(f"/api/link/connectors/{cid}")
        assert resp.status_code == 200


class TestLinkEvents:
    def test_list_events(self, client):
        resp = client.get("/api/link/events")
        assert resp.status_code == 200
