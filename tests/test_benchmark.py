"""Integration tests for Benchmark API (/api/benchmark) — organ performance benchmarks."""

import pytest


class TestBenchmarkHealth:
    def test_health(self, client):
        resp = client.get("/api/benchmark/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestBenchmarkStats:
    def test_stats(self, client):
        resp = client.get("/api/benchmark/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestBenchmarkTargets:
    def test_targets(self, client):
        resp = client.get("/api/benchmark/targets")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))


class TestBenchmarkRun:
    def test_run_missing_body(self, client):
        resp = client.post("/api/benchmark/run", json={})
        assert resp.status_code in (200, 400, 422)

    def test_run_with_target(self, client):
        resp = client.post(
            "/api/benchmark/run",
            json={"target": "health", "iterations": 1},
        )
        assert resp.status_code in (200, 400, 422)

    def test_quick_benchmark(self, client):
        resp = client.post("/api/benchmark/quick/health")
        assert resp.status_code in (200, 400, 404, 422)


class TestBenchmarkHistory:
    def test_history(self, client):
        resp = client.get("/api/benchmark/history")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_history_runs(self, client):
        resp = client.get("/api/benchmark/history/runs")
        assert resp.status_code == 200

    def test_latest(self, client):
        resp = client.get("/api/benchmark/latest")
        assert resp.status_code in (200, 404)

    def test_comparison(self, client):
        resp = client.get("/api/benchmark/comparison")
        assert resp.status_code in (200, 404)

    def test_delete_history(self, client):
        resp = client.delete("/api/benchmark/history")
        assert resp.status_code in (200, 204, 404)


class TestBenchmarkCancel:
    def test_cancel_nonexistent(self, client):
        resp = client.post("/api/benchmark/cancel/nonexistent-run-id-99999")
        assert resp.status_code in (200, 404)
