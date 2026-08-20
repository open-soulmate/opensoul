"""Integration tests for Plugins API (/api/plugins) — plugin management."""

import pytest


class TestPluginsHealth:
    def test_health(self, client):
        resp = client.get("/api/plugins/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestPluginsList:
    def test_list_plugins(self, client):
        resp = client.get("/api/plugins")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, (list, dict))

    def test_sidebar(self, client):
        resp = client.get("/api/plugins/sidebar")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, list)


class TestPluginsCRUD:
    def test_get_nonexistent(self, client):
        resp = client.get("/api/plugins/99999")
        assert resp.status_code in (200, 401, 404, 422)

    def test_install_missing(self, client):
        resp = client.post("/api/plugins/install", json={})
        assert resp.status_code in (200, 400, 401, 422)

    def test_patch_nonexistent(self, client):
        resp = client.patch(
            "/api/plugins/99999",
            json={"enabled": False},
        )
        assert resp.status_code in (200, 401, 404, 422)

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/plugins/99999")
        assert resp.status_code in (200, 401, 404, 422)

    def test_config_nonexistent(self, client):
        resp = client.post(
            "/api/plugins/99999/config",
            json={},
        )
        assert resp.status_code in (200, 400, 401, 404, 422)
