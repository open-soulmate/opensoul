"""Integration tests for OpenMarrow (骨髓) — backup, restore, export/import."""

import pytest


@pytest.fixture()
def backup_src(tmp_path):
    """备份测试源目录：固定2个小文件的专用目录。
    不用tempfile.gettempdir()——整目录tar /tmp（本机724MB）会让backup请求
    超过客户端30s预算（httpx.ReadTimeout），大小相关=天然flaky。"""
    d = tmp_path / "marrow_src"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("alpha", encoding="utf-8")
    (d / "sub" / "b.txt").write_text("beta", encoding="utf-8")
    return str(d)


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

    def test_create_and_delete_backup(self, client, backup_src):
        resp = client.post(
            "/api/marrow/backup",
            json={
                "name": "test_backup_integration",
                "description": "Created by integration test",
                "source_dirs": [backup_src],
            },
        )
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
        resp = client.post(
            "/api/marrow/export",
            json={
                "format": "json",
                "data": [{"type": "knowledge", "name": "test"}],
            },
        )
        assert resp.status_code == 200


class TestMarrowSchedules:
    def test_list_schedules(self, client):
        resp = client.get("/api/marrow/schedules")
        assert resp.status_code == 200
        assert "schedules" in resp.json()

    def test_create_and_delete_schedule(self, client, backup_src):
        resp = client.post(
            "/api/marrow/schedules",
            json={
                "name": "test_schedule",
                "source_dirs": [backup_src],
                "interval": "daily",
                "description": "Integration test schedule",
                "tags": ["test"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "schedule_id" in data
        assert data["name"] == "test_schedule"
        assert data["cron_expr"] == "daily"
        schedule_id = data["schedule_id"]

        # List schedules — should include our new one
        resp = client.get("/api/marrow/schedules")
        assert resp.status_code == 200
        ids = [s["schedule_id"] for s in resp.json()["schedules"]]
        assert schedule_id in ids

        # Toggle off
        resp = client.put(f"/api/marrow/schedules/{schedule_id}/toggle", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        # Toggle on
        resp = client.put(f"/api/marrow/schedules/{schedule_id}/toggle", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

        # Delete
        resp = client.delete(f"/api/marrow/schedules/{schedule_id}")
        assert resp.status_code == 200

    def test_create_schedule_invalid_interval(self, client):
        resp = client.post(
            "/api/marrow/schedules",
            json={
                "name": "bad_schedule",
                "source_dirs": ["/tmp"],
                "interval": "invalid",
            },
        )
        assert resp.status_code == 400

    def test_health_includes_scheduler(self, client):
        resp = client.get("/api/marrow/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "scheduler" in data
        assert "running" in data["scheduler"]
