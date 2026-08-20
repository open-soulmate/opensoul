"""Integration tests for Pipeline API."""


class TestPipelineEndpoints:
    def test_list_pipeline_types(self, client):
        resp = client.get("/api/pipeline/types")
        assert resp.status_code == 200
        data = resp.json()
        assert "types" in data
        assert isinstance(data["types"], list)
        assert "stages" in data

    def test_pipeline_history(self, client):
        resp = client.get("/api/pipeline/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "pipelines" in data
        assert isinstance(data["pipelines"], list)
        assert "total" in data

    def test_pipeline_stats(self, client):
        resp = client.get("/api/pipeline/stats")
        assert resp.status_code == 200

    def test_get_nonexistent_pipeline(self, client):
        resp = client.get("/api/pipeline/history/nonexistent-id")
        assert resp.status_code == 404
