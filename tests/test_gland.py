"""Integration tests for OpenGland (腺体) — model gateway."""

import pytest


class TestGlandHealth:
    def test_health(self, client):
        resp = client.get("/api/gland/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenGland"


class TestGlandModels:
    def test_list_models(self, client):
        resp = client.get("/api/gland/models")
        assert resp.status_code == 200

    def test_providers(self, client):
        resp = client.get("/api/gland/providers")
        assert resp.status_code == 200

    def test_usage(self, client):
        resp = client.get("/api/gland/usage")
        assert resp.status_code == 200

    def test_keys(self, client):
        resp = client.get("/api/gland/keys")
        assert resp.status_code == 200
