"""Integration tests for OpenTrajectory — agent execution trace and replay."""


class TestTrajectoryHealth:
    def test_health(self, client):
        resp = client.get("/api/trajectory/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "trajectory"

    def test_stats(self, client):
        resp = client.get("/api/trajectory/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_sessions" in data
        assert "total_events" in data

    def test_event_types(self, client):
        resp = client.get("/api/trajectory/event-types")
        assert resp.status_code == 200


class TestTrajectorySessions:
    def test_create_list_get_end_session(self, client):
        # Create
        resp = client.post(
            "/api/trajectory/sessions",
            json={
                "agent_id": "test-agent",
                "task_description": "Integration test session",
                "tags": ["test"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        session_id = data["id"]

        # List
        resp = client.get("/api/trajectory/sessions")
        assert resp.status_code == 200

        # List with filter
        resp = client.get("/api/trajectory/sessions?agent_id=test-agent&limit=5")
        assert resp.status_code == 200

        # Get specific
        resp = client.get(f"/api/trajectory/sessions/{session_id}")
        assert resp.status_code == 200

        # End session
        resp = client.post(f"/api/trajectory/sessions/{session_id}/end")
        assert resp.status_code == 200

        # Delete
        resp = client.delete(f"/api/trajectory/sessions/{session_id}")
        assert resp.status_code == 200


class TestTrajectoryEvents:
    def test_record_and_list_events(self, client):
        # Create session
        sess = client.post(
            "/api/trajectory/sessions",
            json={"agent_id": "test-agent", "task_description": "event test"},
        )
        session_id = sess.json()["id"]

        # Record event
        resp = client.post(
            f"/api/trajectory/sessions/{session_id}/events",
            json={
                "event_type": "thought",
                "agent_id": "test-agent",
                "content": "Testing trajectory event recording",
                "metadata": {"test": True},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data

        # List events for session
        resp = client.get(f"/api/trajectory/sessions/{session_id}/events")
        assert resp.status_code == 200

        # Cleanup
        client.delete(f"/api/trajectory/sessions/{session_id}")

    def test_batch_events(self, client):
        sess = client.post(
            "/api/trajectory/sessions",
            json={"agent_id": "test-agent", "task_description": "batch test"},
        )
        session_id = sess.json()["id"]

        resp = client.post(
            f"/api/trajectory/sessions/{session_id}/events/batch",
            json={
                "events": [
                    {"event_type": "thought", "content": "step 1"},
                    {"event_type": "action", "content": "step 2"},
                ]
            },
        )
        assert resp.status_code == 200

        client.delete(f"/api/trajectory/sessions/{session_id}")


class TestTrajectorySearch:
    def test_search(self, client):
        resp = client.get("/api/trajectory/search?q=test&limit=5")
        assert resp.status_code == 200


class TestTrajectoryReplay:
    def test_replay_nonexistent(self, client):
        resp = client.get("/api/trajectory/sessions/nonexistent-id/replay")
        # Should return 404 or empty
        assert resp.status_code in (200, 404)


class TestTrajectoryAnalytics:
    def test_tool_analytics(self, client):
        resp = client.get("/api/trajectory/analytics/tools")
        assert resp.status_code == 200

    def test_agent_analytics(self, client):
        resp = client.get("/api/trajectory/analytics/agents")
        assert resp.status_code == 200

    def test_event_type_analytics(self, client):
        resp = client.get("/api/trajectory/analytics/event-types")
        assert resp.status_code == 200

    def test_token_analytics(self, client):
        resp = client.get("/api/trajectory/analytics/tokens")
        assert resp.status_code == 200


class TestTrajectoryV2:
    def test_v2_health(self, client):
        resp = client.get("/api/trajectory-v2/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "trajectory-v2"
