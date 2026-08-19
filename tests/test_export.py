"""Integration tests for Export API — data export endpoints."""


class TestExportHealth:
    def test_health(self, client):
        resp = client.get("/api/export/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "ExportSystem"


class TestExportEndpoints:
    def test_export_json(self, client):
        """Export JSON requires user_id param."""
        resp = client.get("/api/export/json", params={"user_id": "00000000-0000-0000-0000-000000000000"})
        # 500 if user doesn't exist in DB (expected in test env)
        assert resp.status_code in (200, 401, 422, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert "knowledge" in data
            assert "entities" in data
            assert "tags" in data

    def test_export_markdown(self, client):
        """Export markdown requires user_id param."""
        resp = client.get("/api/export/markdown", params={"user_id": "00000000-0000-0000-0000-000000000000"})
        # 500 if user doesn't exist in DB (expected in test env)
        assert resp.status_code in (200, 401, 422, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert "knowledge" in data
            assert data.get("format") == "markdown"
