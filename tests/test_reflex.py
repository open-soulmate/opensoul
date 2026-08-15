"""Integration tests for OpenReflex (条件反射) — high-speed response cache."""

import pytest


class TestReflexHealth:
    def test_health(self, client):
        resp = client.get("/api/reflex/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenReflex"


class TestReflexCache:
    def test_create_lookup_delete(self, client):
        # Create (API requires "response" not "answer")
        resp = client.post("/api/reflex/cache", json={
            "query": "What is the capital of France?",
            "response": "Paris",
            "category": "geography",
            "source": "test",
        })
        assert resp.status_code == 200
        data = resp.json()
        eid = data["entry_id"]

        # Lookup
        resp = client.post("/api/reflex/lookup", json={
            "query": "capital of France",
        })
        assert resp.status_code == 200

        # List
        resp = client.get("/api/reflex/cache")
        assert resp.status_code == 200

        # Get specific
        resp = client.get(f"/api/reflex/cache/{eid}")
        assert resp.status_code == 200

        # Delete
        resp = client.delete(f"/api/reflex/cache/{eid}")
        assert resp.status_code == 200


class TestReflexConfig:
    def test_get_config(self, client):
        resp = client.get("/api/reflex/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "similarity_threshold" in data

    def test_update_config(self, client):
        resp = client.put("/api/reflex/config", json={
            "similarity_threshold": 0.8,
            "default_ttl_seconds": 86400,
        })
        assert resp.status_code == 200
