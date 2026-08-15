"""Integration tests for OpenCortex (皮层) — advanced reasoning, task planning."""

import pytest


class TestCortexHealth:
    def test_health(self, client):
        resp = client.get("/api/cortex/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
