"""Integration tests for Pipeline API (/api/pipeline) — cross-organ file processing."""

import pytest


class TestPipelineHealth:
    def test_health(self, client):
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

    def test_types_has_pipelines(self, client):
        resp = client.get("/api/pipeline/types")
        assert resp.status_code == 200
        data = resp.json()
        if isinstance(data, list):
            assert len(data) > 0
        elif isinstance(data, dict):
            assert len(data) > 0


class TestPipelineStats:
    def test_stats(self, client):
        resp = client.get("/api/pipeline/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_stats_has_total(self, client):
        resp = client.get("/api/pipeline/stats")
        assert resp.status_code == 200
        data = resp.json()
        # Should have some stats fields
        assert any(k in data for k in ("total", "total_runs", "completed", "count"))


class TestPipelineHistory:
    def test_history_returns_list(self, client):
        resp = client.get("/api/pipeline/history")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_history_nonexistent_id(self, client):
        resp = client.get("/api/pipeline/history/nonexistent-pipeline-id-12345")
        assert resp.status_code in (200, 404)

    def test_history_with_limit(self, client):
        resp = client.get("/api/pipeline/history?limit=5")
        assert resp.status_code == 200


class TestPipelineRun:
    def test_run_missing_body(self, client):
        resp = client.post("/api/pipeline/run", json={})
        assert resp.status_code in (400, 422)

    def test_run_nonexistent_file(self, client):
        resp = client.post(
            "/api/pipeline/run",
            json={"file_id": "nonexistent-file-id-99999", "pipeline": "auto"},
        )
        assert resp.status_code in (200, 400, 404, 422)

    def test_run_invalid_pipeline_type(self, client):
        resp = client.post(
            "/api/pipeline/run",
            json={"file_id": "test", "pipeline": "invalid_type_xyz"},
        )
        assert resp.status_code in (200, 400, 404, 422)


class TestPipelineUpload:
    def test_upload_no_file(self, client):
        resp = client.post("/api/pipeline/upload")
        assert resp.status_code in (400, 422)

    def test_upload_empty_file(self, client):
        resp = client.post(
            "/api/pipeline/upload",
            files={"file": ("test.txt", b"", "text/plain")},
        )
        # Should handle empty files gracefully
        assert resp.status_code in (200, 400, 422)
