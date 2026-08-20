"""Integration tests for Download API (/api/download) — plugin management and downloads."""

import pytest


class TestDownloadHealth:
    def test_health(self, client):
        resp = client.get("/api/download/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestDownloadPlugins:
    def test_list_plugins(self, client):
        resp = client.get("/api/download/plugins")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_install_nonexistent_plugin(self, client):
        resp = client.post("/api/download/plugins/nonexistent-plugin-xyz/install")
        assert resp.status_code in (200, 400, 404)

    def test_update_nonexistent_plugin(self, client):
        resp = client.post("/api/download/plugins/nonexistent-plugin-xyz/update")
        assert resp.status_code in (200, 400, 404)

    def test_update_all(self, client):
        resp = client.post("/api/download/plugins/update-all")
        assert resp.status_code in (200, 400)


class TestDownloadStatus:
    def test_status(self, client):
        resp = client.get("/api/download/status")
        assert resp.status_code in (200, 404)
