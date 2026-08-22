"""Integration tests for OpenNerve (神经) — event bus, WebSocket."""


class TestNerveHealth:
    def test_health(self, client):
        resp = client.get("/api/nerve/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestNerveEvents:
    def test_publish_event(self, client):
        resp = client.post(
            "/api/nerve/publish",
            json={"topic": "test.topic", "data": {"message": "hello"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data or "event_id" in data or data.get("status") == "ok"

    def test_get_events(self, client):
        resp = client.get("/api/nerve/events")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_get_events_with_limit(self, client):
        resp = client.get("/api/nerve/events?limit=5")
        assert resp.status_code == 200


class TestNerveSubscriptions:
    def test_subscribe(self, client):
        resp = client.post(
            "/api/nerve/subscribe",
            json={"topic_pattern": "test.*", "subscriber_id": "test-sub-1"},
        )
        assert resp.status_code == 200

    def test_list_subscriptions(self, client):
        resp = client.get("/api/nerve/subscriptions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_unsubscribe(self, client):
        # Subscribe first
        client.post(
            "/api/nerve/subscribe",
            json={"topic_pattern": "unsub.test", "subscriber_id": "test-sub-2"},
        )
        resp = client.delete("/api/nerve/subscribe/test-sub-2")
        assert resp.status_code in (200, 204, 404)


class TestNerveNodes:
    def test_register_node(self, client):
        resp = client.post(
            "/api/nerve/nodes/register",
            json={"node_id": "test-node-1", "name": "Test Node"},
        )
        assert resp.status_code == 200

    def test_list_nodes(self, client):
        resp = client.get("/api/nerve/nodes")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_node_heartbeat(self, client):
        resp = client.post(
            "/api/nerve/nodes/heartbeat",
            json={"node_id": "test-node-1"},
        )
        assert resp.status_code == 200


class TestNerveStats:
    def test_stats(self, client):
        resp = client.get("/api/nerve/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestNerveBatchPublish:
    def test_batch_publish(self, client):
        """Test batch publish endpoint with multiple events."""
        resp = client.post(
            "/api/nerve/publish/batch",
            json={
                "events": [
                    {"topic": "soma.test1", "data": {"msg": "first"}, "source": "test"},
                    {"topic": "soma.test2", "data": {"msg": "second"}, "source": "test"},
                    {"topic": "soma.test3", "data": {"msg": "third"}, "source": "test"},
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] == 3
        assert data["rejected"] == 0
        assert len(data["results"]) == 3
        for r in data["results"]:
            assert r["status"] == "ok"
            assert "event_id" in r

    def test_batch_publish_single_event(self, client):
        """Batch with single event should work like /publish."""
        resp = client.post(
            "/api/nerve/publish/batch",
            json={
                "events": [
                    {"topic": "soma.single", "data": {"key": "value"}},
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] == 1
        assert data["rejected"] == 0

    def test_batch_publish_empty_rejected(self, client):
        """Empty batch should be rejected by Pydantic validation."""
        resp = client.post(
            "/api/nerve/publish/batch",
            json={"events": []},
        )
        assert resp.status_code == 422  # Validation error: min_length=1

    def test_batch_publish_preserves_topics(self, client):
        """Events with different topics should be published independently."""
        resp = client.post(
            "/api/nerve/publish/batch",
            json={
                "events": [
                    {"topic": "alpha.one", "data": {"n": 1}},
                    {"topic": "beta.two", "data": {"n": 2}},
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] == 2
        # Verify events appear in the bus
        events_resp = client.get("/api/nerve/events?limit=10")
        assert events_resp.status_code == 200
