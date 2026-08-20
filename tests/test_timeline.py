"""Integration tests for OpenTimeline — persistent cross-component event timeline."""


class TestTimelineHealth:
    def test_health(self, client):
        resp = client.get("/api/timeline/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenTimeline"
        assert "total_events" in data
        assert "recent_24h" in data


class TestTimelineEvents:
    def test_record_and_get_event(self, client):
        # Record
        resp = client.post(
            "/api/timeline/record",
            json={
                "organ": "test",
                "emoji": "🧪",
                "event_type": "test_event",
                "summary": "Integration test event",
                "detail": {"test": True},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "duplicate")
        event_id = data["event_id"]

        # Get specific event
        resp = client.get(f"/api/timeline/events/{event_id}")
        assert resp.status_code == 200

        # Delete
        resp = client.delete(f"/api/timeline/events/{event_id}")
        assert resp.status_code == 200

    def test_list_events(self, client):
        resp = client.get("/api/timeline/events?limit=5")
        assert resp.status_code == 200

    def test_list_events_with_filters(self, client):
        resp = client.get("/api/timeline/events?organ=test&limit=5")
        assert resp.status_code == 200

    def test_get_nonexistent_event(self, client):
        resp = client.get("/api/timeline/events/nonexistent_id")
        assert resp.status_code == 404


class TestTimelineStats:
    def test_stats(self, client):
        resp = client.get("/api/timeline/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_events" in data
        assert "by_organ" in data
        assert "by_type" in data

    def test_organs(self, client):
        resp = client.get("/api/timeline/organs")
        assert resp.status_code == 200

    def test_types(self, client):
        resp = client.get("/api/timeline/types")
        assert resp.status_code == 200
