"""Integration tests for Admin Actions API."""


class TestAdminHealth:
    def test_health(self, client):
        resp = client.get("/api/admin/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "AdminActions"


class TestAdminOverview:
    def test_system_overview(self, client):
        resp = client.get("/api/admin/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "timestamp" in data

    def test_system_report(self, client):
        resp = client.get("/api/admin/report")
        assert resp.status_code == 200

    def test_export_config(self, client):
        resp = client.get("/api/admin/export/config")
        assert resp.status_code == 200


class TestAdminCacheOps:
    def test_clear_all_caches(self, client):
        resp = client.post("/api/admin/caches/clear")
        assert resp.status_code == 200

    def test_cleanup_expired(self, client):
        resp = client.post("/api/admin/cleanup")
        assert resp.status_code == 200
