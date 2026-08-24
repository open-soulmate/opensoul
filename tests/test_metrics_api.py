"""Integration tests for Metrics API — Prometheus metrics export."""

import re


class TestMetricsHealth:
    def test_metrics_health(self, client):
        resp = client.get("/metrics/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenMetrics"
        assert "tracked_endpoints" in data
        assert "tracked_organs" in data


class TestMetricsEndpoint:
    def test_prometheus_metrics(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        # Should contain Prometheus-format metrics
        assert "opensoul_" in text or "# HELP" in text or "ok" in text.lower()

    def test_metrics_content_type(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        assert "text/plain" in content_type

    def test_metrics_contains_info(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        assert "opensoul_info" in text

    def test_metrics_contains_uptime(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        assert "opensoul_uptime_seconds" in text

    def test_metrics_contains_system_info(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        assert "opensoul_system_cpu_count" in text

    def test_metrics_contains_disk_info(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        assert "opensoul_system_disk_" in text

    def test_metrics_format_valid(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        # Check basic Prometheus format: # HELP lines followed by metric lines
        lines = text.strip().split("\n")
        help_lines = [l for l in lines if l.startswith("# HELP")]
        type_lines = [l for l in lines if l.startswith("# TYPE")]
        assert len(help_lines) > 0
        assert len(type_lines) > 0
        # Each TYPE should have a matching HELP
        for type_line in type_lines:
            metric_name = type_line.split()[2]
            assert any(metric_name in hl for hl in help_lines)

    def test_metrics_http_requests_counter(self, client):
        # Make a request first to ensure metrics are recorded
        client.get("/metrics/health")
        resp = client.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        # Should have HTTP request metrics
        assert "opensoul_http_requests_total" in text or "opensoul_http_request" in text

    def test_metrics_process_memory(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        assert "opensoul_process_resident_memory_bytes" in text

    def test_metrics_process_cpu(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        assert "opensoul_process_cpu_seconds_total" in text
