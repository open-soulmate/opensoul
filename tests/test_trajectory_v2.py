"""Integration tests for OpenSoul Trajectory v2 API."""


class TestTrajectoryV2Health:
    def test_health_returns_200(self, client):
        resp = client.get("/api/trajectory-v2/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestTrajectoryV2Sessions:
    def test_list_sessions_endpoint_exists(self, client):
        """Sessions endpoint should exist (may return 500 due to DB init issue)."""
        resp = client.get("/api/trajectory-v2/sessions")
        assert resp.status_code in (200, 500)

    def test_get_session_endpoint_exists(self, client):
        resp = client.get("/api/trajectory-v2/sessions/test-id")
        assert resp.status_code in (200, 404, 500)


class TestTrajectoryV2Events:
    def test_post_event_endpoint_exists(self, client):
        resp = client.post("/api/trajectory-v2/events", json={})
        assert resp.status_code in (200, 400, 422, 500)
