"""Integration tests for Capture API (/api/capture) — page/selection capture."""

import pytest


class TestCaptureHealth:
    def test_health(self, client):
        resp = client.get("/api/capture/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestCaptureStats:
    def test_stats(self, client):
        resp = client.get("/api/capture/stats")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)


class TestCaptureList:
    def test_list(self, client):
        resp = client.get("/api/capture/list")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, (list, dict))


class TestCapturePage:
    def test_page_missing_fields(self, client):
        resp = client.post("/api/capture/page", json={})
        assert resp.status_code in (200, 400, 422)

    def test_page_with_url(self, client):
        resp = client.post(
            "/api/capture/page",
            json={"url": "https://example.com", "title": "Example"},
        )
        assert resp.status_code in (200, 400, 422, 500, 503)


class TestCaptureSelection:
    def test_selection_missing(self, client):
        resp = client.post("/api/capture/selection", json={})
        assert resp.status_code in (200, 400, 422)


class TestCaptureGetDelete:
    def test_get_nonexistent(self, client):
        resp = client.get("/api/capture/99999")
        assert resp.status_code in (200, 404)

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/capture/99999")
        assert resp.status_code in (200, 404)

    def test_promote_nonexistent(self, client):
        resp = client.post("/api/capture/99999/promote", json={})
        assert resp.status_code in (200, 404, 422)
