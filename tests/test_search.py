"""Integration tests for OpenSearch — unified search across all components."""


class TestSearchHealth:
    def test_health(self, client):
        resp = client.get("/api/search/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_stats(self, client):
        resp = client.get("/api/search/stats")
        assert resp.status_code == 200
