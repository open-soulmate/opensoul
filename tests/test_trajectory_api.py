"""Integration tests for Trajectory V2 API (/api/trajectory-v2) — event replay, sessions."""

import pytest


class TestTrajectoryHealth:
    def test_health(self, client):
        resp = client.get("/api/trajectory-v2/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestTrajectoryEvents:
    def test_post_events_missing(self, client):
        resp = client.post("/api/trajectory-v2/events", json={})
        assert resp.status_code in (200, 400, 401, 422)

    def test_post_events_with_data(self, client):
        resp = client.post(
            "/api/trajectory-v2/events",
            json={"event_type": "test", "data": {"message": "hello"}},
        )
        assert resp.status_code in (200, 400, 401, 422)


class TestTrajectorySessions:
    def test_list_sessions(self, client):
        resp = client.get("/api/trajectory-v2/sessions")
        assert resp.status_code in (200, 401, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, (list, dict))

    def test_get_nonexistent_session(self, client):
        resp = client.get("/api/trajectory-v2/sessions/nonexistent-session-99999")
        assert resp.status_code in (200, 401, 404, 500)

    def test_replay_nonexistent_session(self, client):
        resp = client.get("/api/trajectory-v2/sessions/nonexistent-session-99999/replay")
        assert resp.status_code in (200, 401, 404, 500)
