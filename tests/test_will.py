"""Integration tests for OpenWill (意志) — workflow engine."""

import pytest


class TestWillHealth:
    def test_health(self, client):
        resp = client.get("/api/will/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
