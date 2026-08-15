"""Integration tests for OpenPulse (脉搏) — precision timer signals."""

import pytest


class TestPulseHealth:
    def test_health(self, client):
        resp = client.get("/api/pulse/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenPulse"


class TestPulseSignals:
    def test_create_list_delete_signal(self, client):
        resp = client.post("/api/pulse/signals", json={
            "name": "test_signal",
            "interval_seconds": 60,
        })
        assert resp.status_code == 200
        data = resp.json()
        sid = data["signal_id"]

        resp = client.get("/api/pulse/signals")
        assert resp.status_code == 200

        resp = client.get(f"/api/pulse/signals/{sid}")
        assert resp.status_code == 200

        # Pause/Resume
        resp = client.post(f"/api/pulse/signals/{sid}/pause")
        assert resp.status_code == 200
        resp = client.post(f"/api/pulse/signals/{sid}/resume")
        assert resp.status_code == 200

        # Delete
        resp = client.delete(f"/api/pulse/signals/{sid}")
        assert resp.status_code == 200


class TestPulseTick:
    def test_manual_tick(self, client):
        # tick requires signal_id
        resp = client.get("/api/pulse/signals")
        signals = resp.json() if isinstance(resp.json(), list) else resp.json().get("signals", [])
        if signals:
            sid = signals[0]["signal_id"]
            resp = client.post(f"/api/pulse/signals/{sid}/tick")
            assert resp.status_code == 200

    def test_get_ticks(self, client):
        resp = client.get("/api/pulse/ticks")
        assert resp.status_code == 200

    def test_stats(self, client):
        resp = client.get("/api/pulse/stats")
        assert resp.status_code == 200
