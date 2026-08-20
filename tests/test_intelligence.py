"""Tests for OpenIntelligence — analytics and insights engine."""


class TestIntelligenceHealth:
    def test_health(self, client):
        resp = client.get("/api/intelligence/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestIntelligenceStats:
    def test_stats(self, client):
        resp = client.get("/api/intelligence/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestIntelligenceSummary:
    def test_summary(self, client):
        resp = client.get("/api/intelligence/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestIntelligenceInsights:
    def test_insights(self, client):
        resp = client.get("/api/intelligence/insights")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))


class TestIntelligenceComponents:
    def test_components(self, client):
        resp = client.get("/api/intelligence/components")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))


class TestIntelligenceRecommendations:
    def test_recommendations(self, client):
        resp = client.get("/api/intelligence/recommendations")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))


class TestIntelligenceTrends:
    def test_trends(self, client):
        resp = client.get("/api/intelligence/trends/system")
        assert resp.status_code in (200, 404)
