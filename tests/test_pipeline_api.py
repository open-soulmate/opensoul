"""Integration tests for OpenSoul Pipeline API."""


class TestPipelineHealth:
    def test_health_returns_200(self, client):
        resp = client.get("/api/pipeline/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestPipelineTypes:
    def test_list_types(self, client):
        resp = client.get("/api/pipeline/types")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))


class TestPipelineHistory:
    def test_history_returns_list(self, client):
        resp = client.get("/api/pipeline/history")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))


class TestPipelineStats:
    def test_stats_returns_200(self, client):
        resp = client.get("/api/pipeline/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
