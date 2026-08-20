"""Tests for OpenDiagnostics — system diagnostics and health checks."""


class TestDiagnosticsHealth:
    def test_health(self, client):
        resp = client.get("/api/diagnostics/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestDiagnosticsInfo:
    def test_info(self, client):
        resp = client.get("/api/diagnostics/info")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestDiagnosticsOrgans:
    def test_list_organs(self, client):
        resp = client.get("/api/diagnostics/organs")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_get_nonexistent_organ(self, client):
        resp = client.get("/api/diagnostics/organs/nonexistent_organ_xyz")
        assert resp.status_code in (404, 200)


class TestDiagnosticsCheckAll:
    def test_check_all(self, client):
        resp = client.get("/api/diagnostics/check-all")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestDiagnosticsStats:
    def test_stats(self, client):
        resp = client.get("/api/diagnostics/stats")
        assert resp.status_code == 200
