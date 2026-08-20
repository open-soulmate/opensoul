"""Tests for SystemOverview — aggregated system status dashboard.

Note: The route prefix is /api/system (not /api/system-overview).
"""


class TestSystemOverviewHealth:
    def test_health(self, client):
        resp = client.get("/api/system/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestSystemOverview:
    def test_overview(self, client):
        resp = client.get("/api/system/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_quick(self, client):
        resp = client.get("/api/system/quick")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestBootstrap:
    def test_bootstrap_status(self, client):
        resp = client.get("/api/system/bootstrap/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
