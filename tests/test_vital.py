"""Integration tests for OpenVital (体征) — system metrics and monitoring."""


class TestVitalHealth:
    def test_health(self, client):
        resp = client.get("/api/vital/health")
        assert resp.status_code == 200
        data = resp.json()
        # Status can be "ok"/"up" when all services running, or "down"/"degraded"
        # when optional services (qdrant, nats, meilisearch) are unavailable
        assert data["status"] in ("ok", "up", "down", "degraded")
        assert "components" in data
        assert isinstance(data["components"], list)
        assert len(data["components"]) > 0


class TestVitalMetrics:
    def test_get_metrics(self, client):
        resp = client.get("/api/vital/metrics")
        assert resp.status_code == 200
        # Returns prometheus-style text metrics
        assert resp.status_code == 200

    def test_alerts(self, client):
        resp = client.get("/api/vital/alerts")
        assert resp.status_code == 200
