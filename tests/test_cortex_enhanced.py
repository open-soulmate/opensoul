"""Integration tests for Cortex Enhanced API (/api/cortex) — GraphRAG, recommendations, quality."""

import pytest


class TestCortexEnhancedHealth:
    def test_enhanced_health(self, client):
        resp = client.get("/api/cortex/enhanced/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestCortexGraphRAG:
    def test_graphrag_build_missing(self, client):
        resp = client.post("/api/cortex/graphrag/build", json={})
        assert resp.status_code in (200, 400, 422)

    def test_graphrag_query_missing(self, client):
        resp = client.post("/api/cortex/graphrag/query", json={})
        assert resp.status_code in (200, 400, 422)

    def test_graphrag_query_with_text(self, client):
        resp = client.post(
            "/api/cortex/graphrag/query",
            json={"query": "test query"},
        )
        assert resp.status_code in (200, 400, 422)

    def test_graphrag_extract_missing(self, client):
        resp = client.post("/api/cortex/graphrag/extract", json={})
        assert resp.status_code in (200, 400, 422)


class TestCortexRecommend:
    def test_trending(self, client):
        resp = client.get("/api/cortex/recommend/trending")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_recent(self, client):
        resp = client.get("/api/cortex/recommend/recent")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_recommend_nonexistent(self, client):
        resp = client.get("/api/cortex/recommend/nonexistent-knowledge-id-99999")
        assert resp.status_code in (200, 404)


class TestCortexQuality:
    def test_quality_report(self, client):
        resp = client.get("/api/cortex/quality/report")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_quality_batch(self, client):
        resp = client.get("/api/cortex/quality/batch")
        assert resp.status_code in (200, 400)

    def test_quality_score_nonexistent(self, client):
        resp = client.get("/api/cortex/quality/score/nonexistent-knowledge-id-99999")
        assert resp.status_code in (200, 404)
