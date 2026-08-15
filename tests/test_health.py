"""Integration tests for OpenSoul health endpoints."""

import pytest


class TestHealthAll:
    def test_health_all_returns_200(self, client):
        resp = client.get("/api/health/all")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["total"] >= 25
        assert data["healthy"] >= 25

    def test_health_all_organs_present(self, client):
        resp = client.get("/api/health/all")
        organs = resp.json()["organs"]
        expected = [
            "soul", "cortex", "nerve", "vein", "sense", "will",
            "vital", "gland", "immune", "marrow", "gene", "echo",
            "mirror", "link", "hippo", "reflex", "heredity", "pulse",
            "nest", "limb", "voice", "vision", "mind",
        ]
        for organ in expected:
            assert organ in organs, f"Missing organ: {organ}"
            assert organs[organ] == "ok", f"Organ {organ} is not healthy"
