"""Integration tests for Healer API (/api/healer) — self-diagnosis and repair."""

import pytest


class TestHealerHealth:
    def test_health(self, client):
        resp = client.get("/api/healer/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestHealerStats:
    def test_stats(self, client):
        resp = client.get("/api/healer/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestHealerOrgans:
    def test_organs(self, client):
        resp = client.get("/api/healer/organs")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))


class TestHealerDiagnose:
    def test_diagnose_health(self, client):
        resp = client.post("/api/healer/diagnose/health")
        assert resp.status_code in (200, 400, 404)

    def test_diagnose_nonexistent_organ(self, client):
        resp = client.post("/api/healer/diagnose/nonexistent_organ_xyz")
        assert resp.status_code in (200, 400, 404)

    def test_diagnose_all(self, client):
        resp = client.post("/api/healer/diagnose-all")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (dict, list))


class TestHealerHeal:
    def test_heal_health(self, client):
        resp = client.post("/api/healer/heal/health")
        assert resp.status_code in (200, 400, 404)

    def test_heal_nonexistent_organ(self, client):
        resp = client.post("/api/healer/heal/nonexistent_organ_xyz")
        assert resp.status_code in (200, 400, 404)

    def test_heal_all(self, client):
        resp = client.post("/api/healer/heal-all")
        assert resp.status_code == 200


class TestHealerCycle:
    def test_cycle(self, client):
        resp = client.post("/api/healer/cycle")
        assert resp.status_code in (200, 400)


class TestHealerHistory:
    def test_history(self, client):
        resp = client.get("/api/healer/history")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_history_with_limit(self, client):
        resp = client.get("/api/healer/history?limit=5")
        assert resp.status_code == 200
