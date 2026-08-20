"""Integration tests for Event Stream API (/api/events) — streaming, SSE, summaries."""

import pytest


class TestEventStreamHealth:
    def test_health(self, client):
        resp = client.get("/api/events/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestEventStreamStream:
    def test_stream_returns_data(self, client):
        resp = client.get("/api/events/stream")
        assert resp.status_code == 200

    def test_stream_with_limit(self, client):
        resp = client.get("/api/events/stream?limit=10")
        assert resp.status_code == 200

    def test_stream_with_topic_filter(self, client):
        resp = client.get("/api/events/stream?topic=test")
        assert resp.status_code == 200


class TestEventStreamSummary:
    def test_summary(self, client):
        resp = client.get("/api/events/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_stream_summary(self, client):
        resp = client.get("/api/events/stream/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestEventStreamRefresh:
    def test_refresh(self, client):
        resp = client.post("/api/events/stream/refresh")
        assert resp.status_code in (200, 204)


class TestEventStreamSSE:
    def test_sse_endpoint_exists(self, client):
        # SSE is a streaming endpoint; use httpx stream to avoid hanging
        with client.stream("GET", "/api/events/sse", timeout=5) as resp:
            assert resp.status_code in (200, 204)

    def test_sse_clients(self, client):
        resp = client.get("/api/events/sse/clients")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))
