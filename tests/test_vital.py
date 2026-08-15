"""Integration tests for OpenVital (体征) — system metrics and monitoring."""

import pytest


class TestVitalHealth:
    def test_health(self, client):
        resp = client.get("/api/vital/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "up")
        assert "components" in data


class TestVitalMetrics:
    def test_get_metrics(self, client):
        resp = client.get("/api/vital/metrics")
        assert resp.status_code == 200
        # Returns prometheus-style text metrics
        assert resp.status_code == 200

    def test_alerts(self, client):
        resp = client.get("/api/vital/alerts")
        assert resp.status_code == 200
