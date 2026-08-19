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
            json={"topic": "test.*", "subscriber_id": "test-sub-1"},
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
            json={"topic": "unsub.test", "subscriber_id": "test-sub-2"},
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
