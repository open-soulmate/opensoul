"""Integration tests for OpenHeredity (遗传链) — version evolution center."""

import pytest


class TestHeredityHealth:
    def test_health(self, client):
        resp = client.get("/api/heredity/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenHeredity"


class TestHeredityComponents:
    def test_list_components(self, client):
        resp = client.get("/api/heredity/components")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list) or "components" in data

    def test_get_component(self, client):
        resp = client.get("/api/heredity/components")
        components = resp.json() if isinstance(resp.json(), list) else resp.json().get("components", [])
        if components:
            cid = components[0]["component_id"]
            resp = client.get(f"/api/heredity/components/{cid}")
            assert resp.status_code == 200

    def test_dependencies(self, client):
        resp = client.get("/api/heredity/dependencies")
        assert resp.status_code == 200


class TestHeredityMigrations:
    def test_list_migrations(self, client):
        resp = client.get("/api/heredity/migrations")
        assert resp.status_code == 200
