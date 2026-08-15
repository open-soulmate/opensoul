"""Integration tests for OpenImmune (免疫) — security, rate limiting, audit."""

import pytest


class TestImmuneHealth:
    def test_health(self, client):
        resp = client.get("/api/immune/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenImmune"
        assert "modules" in data
        assert "rate_limiter" in data["modules"]
        assert "moderator" in data["modules"]


class TestImmuneModeration:
    def test_moderate_clean_content(self, client):
        resp = client.post("/api/immune/moderate", json={
            "text": "Hello, this is a normal message.",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "is_safe" in data
        assert data["is_safe"] is True

    def test_moderate_requires_text(self, client):
        resp = client.post("/api/immune/moderate", json={})
        assert resp.status_code == 422


class TestImmuneRateLimit:
    def test_rate_limit_check(self, client):
        resp = client.post("/api/immune/rate-limit/check", json={
            "key": "test_user_integration",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "allowed" in data

    def test_rate_limit_stats(self, client):
        resp = client.get("/api/immune/rate-limit/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "tracked_keys" in data

    def test_rate_limit_config_update(self, client):
        resp = client.put("/api/immune/rate-limit/config", json={
            "requests_per_minute": 60,
            "requests_per_hour": 1000,
            "burst_size": 20,
        })
        assert resp.status_code == 200


class TestImmuneIPLists:
    def test_get_lists(self, client):
        resp = client.get("/api/immune/ip/lists")
        assert resp.status_code == 200
        data = resp.json()
        assert "blacklist" in data
        assert "whitelist" in data

    def test_blacklist_add_remove(self, client):
        ip = "192.168.99.99"
        # Add
        resp = client.post("/api/immune/ip/blacklist", json={"ip": ip, "reason": "test"})
        assert resp.status_code == 200
        # Check
        resp = client.get(f"/api/immune/ip/check/{ip}")
        assert resp.status_code == 200
        assert resp.json()["allowed"] is False
        # Remove
        resp = client.delete(f"/api/immune/ip/blacklist/{ip}")
        assert resp.status_code == 200


class TestImmuneAudit:
    def test_audit_log(self, client):
        resp = client.get("/api/immune/audit/log")
        assert resp.status_code == 200

    def test_audit_stats(self, client):
        resp = client.get("/api/immune/audit/stats")
        assert resp.status_code == 200
