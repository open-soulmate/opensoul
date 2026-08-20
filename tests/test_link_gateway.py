"""Integration tests for Link Gateway API (/api/link) — webhooks, connectors."""

import pytest


class TestLinkGateway:
    def test_list_webhooks(self, client):
        resp = client.get("/api/link/webhooks")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_create_webhook_missing(self, client):
        resp = client.post("/api/link/webhooks", json={})
        assert resp.status_code in (200, 400, 422)

    def test_incoming_webhook_nonexistent(self, client):
        resp = client.post(
            "/api/link/webhooks/nonexistent-webhook-id/incoming",
            json={"data": "test"},
        )
        assert resp.status_code in (200, 400, 404)
