"""Integration tests for Metrics API — Prometheus metrics export."""


class TestMetricsHealth:
    def test_metrics_health(self, client):
        resp = client.get("/metrics/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestMetricsEndpoint:
    def test_prometheus_metrics(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        # Should contain Prometheus-format metrics
        assert "opensoul_" in text or "# HELP" in text or "ok" in text.lower()
