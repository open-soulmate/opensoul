"""Integration tests for OpenMarrow (骨髓) — backup, restore, export/import."""

import pytest


class TestMarrowHealth:
    def test_health(self, client):
        resp = client.get("/api/marrow/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenMarrow"
        assert "backup" in data


class TestMarrowBackup:
    def test_list_backups(self, client):
        resp = client.get("/api/marrow/backups")
        assert resp.status_code == 200

    def test_create_and_delete_backup(self, client):
        resp = client.post("/api/marrow/backup", json={
            "name": "test_backup_integration",
            "description": "Created by integration test",
            "source_dirs": ["/home/climbing/opensoul/data"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "backup_id" in data
        backup_id = data["backup_id"]

        # Get specific backup
        resp = client.get(f"/api/marrow/backups/{backup_id}")
        assert resp.status_code == 200

        # Delete
        resp = client.delete(f"/api/marrow/backups/{backup_id}")
        assert resp.status_code == 200


class TestMarrowExport:
    def test_list_exports(self, client):
        resp = client.get("/api/marrow/exports")
        assert resp.status_code == 200

    def test_export_data(self, client):
        resp = client.post("/api/marrow/export", json={
            "format": "json",
            "data": [{"type": "knowledge", "name": "test"}],
        })
        assert resp.status_code == 200
