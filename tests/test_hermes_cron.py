"""Integration tests for Hermes Cron API (/api/cron) — scheduled job management."""

import pytest


class TestCronHealth:
    def test_health(self, client):
        resp = client.get("/api/cron/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestCronList:
    def test_list(self, client):
        resp = client.get("/api/cron/list")
        assert resp.status_code in (200, 401)


class TestCronCreate:
    def test_create_missing_body(self, client):
        resp = client.post("/api/cron/create", json={})
        assert resp.status_code in (200, 400, 401, 422)

    def test_create_with_schedule(self, client):
        resp = client.post(
            "/api/cron/create",
            json={
                "name": "test-cron-job",
                "schedule": "0 * * * *",
                "command": "echo hello",
            },
        )
        assert resp.status_code in (200, 400, 401, 422)


class TestCronJob:
    def test_get_nonexistent(self, client):
        resp = client.get("/api/cron/nonexistent-job-id-99999")
        assert resp.status_code in (200, 401, 404)

    def test_pause_nonexistent(self, client):
        resp = client.post("/api/cron/nonexistent-job-id-99999/pause")
        assert resp.status_code in (200, 401, 404)

    def test_resume_nonexistent(self, client):
        resp = client.post("/api/cron/nonexistent-job-id-99999/resume")
        assert resp.status_code in (200, 401, 404)

    def test_run_nonexistent(self, client):
        resp = client.post("/api/cron/nonexistent-job-id-99999/run")
        assert resp.status_code in (200, 401, 404)

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/cron/nonexistent-job-id-99999")
        assert resp.status_code in (200, 401, 404)

    def test_history_nonexistent(self, client):
        resp = client.get("/api/cron/nonexistent-job-id-99999/history")
        assert resp.status_code in (200, 401, 404)
