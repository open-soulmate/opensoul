"""Integration tests for AI Engine API (/api/ai-engine) — context, harness, loop, graph."""

import pytest


class TestAIEngineHealth:
    def test_health(self, client):
        resp = client.get("/api/ai-engine/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestAIEngineAnalyze:
    def test_analyze_missing_body(self, client):
        resp = client.post("/api/ai-engine/analyze", json={})
        assert resp.status_code in (200, 400, 422)

    def test_analyze_with_text(self, client):
        resp = client.post(
            "/api/ai-engine/analyze",
            json={"task": "write a hello world program"},
        )
        assert resp.status_code in (200, 400, 422)


class TestAIEngineContext:
    def test_get_context(self, client):
        resp = client.get("/api/ai-engine/context")
        assert resp.status_code in (200, 400)

    def test_compress_context(self, client):
        resp = client.post("/api/ai-engine/context/compress", json={})
        assert resp.status_code in (200, 400, 422)


class TestAIEngineHarness:
    def test_routes(self, client):
        resp = client.get("/api/ai-engine/harness/routes")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_check_missing(self, client):
        resp = client.post("/api/ai-engine/harness/check", json={})
        assert resp.status_code in (200, 400, 422)


class TestAIEngineLoop:
    def test_get_loop_nonexistent(self, client):
        resp = client.get("/api/ai-engine/loop/nonexistent-task-id-99999")
        assert resp.status_code in (200, 404)

    def test_reflect_nonexistent(self, client):
        resp = client.post(
            "/api/ai-engine/loop/nonexistent-task-id-99999/reflect",
            json={},
        )
        assert resp.status_code in (200, 400, 404, 422)


class TestAIEngineGraph:
    def test_graph_status(self, client):
        resp = client.get("/api/ai-engine/graph/status")
        assert resp.status_code in (200, 400)

    def test_graph_decompose(self, client):
        resp = client.post("/api/ai-engine/graph/decompose", json={})
        assert resp.status_code in (200, 400, 422)

    def test_graph_decompose_with_task(self, client):
        resp = client.post(
            "/api/ai-engine/graph/decompose",
            json={"task": "build a REST API"},
        )
        assert resp.status_code in (200, 400, 422)


class TestAIEngineStatus:
    def test_status(self, client):
        resp = client.get("/api/ai-engine/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
