"""Unit tests for link/connector.py — bidirectional integration connector."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from src.link.connector import (
    Connector,
    ConnectorStatus,
    ConnectorType,
    IntegrationManager,
    WebhookEvent,
)


class TestConnectorType:
    def test_all_values(self):
        assert ConnectorType.WEBHOOK_IN == "webhook_in"
        assert ConnectorType.WEBHOOK_OUT == "webhook_out"
        assert ConnectorType.REST_API == "rest_api"
        assert ConnectorType.OA_SYSTEM == "oa_system"
        assert ConnectorType.CUSTOM == "custom"


class TestConnectorStatus:
    def test_all_values(self):
        assert ConnectorStatus.ACTIVE == "active"
        assert ConnectorStatus.PAUSED == "paused"
        assert ConnectorStatus.ERROR == "error"
        assert ConnectorStatus.DISABLED == "disabled"


class TestConnectorDataclass:
    def test_defaults(self):
        c = Connector(connector_id="c1", name="Test", type=ConnectorType.WEBHOOK_IN)
        assert c.endpoint == ""
        assert c.secret == ""
        assert c.status == ConnectorStatus.ACTIVE
        assert c.headers == {}
        assert c.config == {}
        assert c.trigger_count == 0
        assert c.error_count == 0


class TestWebhookEvent:
    def test_defaults(self):
        ev = WebhookEvent(event_id="e1", connector_id="c1", timestamp=1.0)
        assert ev.method == "POST"
        assert ev.headers == {}
        assert ev.payload is None
        assert ev.source_ip == ""


class TestIntegrationManager:
    def setup_method(self):
        self.mgr = IntegrationManager()

    def test_create_connector(self):
        c = self.mgr.create_connector("My Webhook", "webhook_in", endpoint="https://example.com")
        assert c.name == "My Webhook"
        assert c.type == ConnectorType.WEBHOOK_IN
        assert c.connector_id.startswith("conn-")

    def test_get_connector(self):
        c = self.mgr.create_connector("Test", "webhook_in")
        found = self.mgr.get_connector(c.connector_id)
        assert found is not None
        assert found.name == "Test"

    def test_get_connector_not_found(self):
        assert self.mgr.get_connector("nonexistent") is None

    def test_list_connectors(self):
        self.mgr.create_connector("A", "webhook_in")
        self.mgr.create_connector("B", "rest_api")
        all_conns = self.mgr.list_connectors()
        assert len(all_conns) == 2

    def test_list_connectors_filter_type(self):
        self.mgr.create_connector("A", "webhook_in")
        self.mgr.create_connector("B", "rest_api")
        filtered = self.mgr.list_connectors(type="webhook_in")
        assert len(filtered) == 1
        assert filtered[0]["name"] == "A"

    def test_list_connectors_filter_status(self):
        c = self.mgr.create_connector("A", "webhook_in")
        self.mgr.update_connector(c.connector_id, status="paused")
        active = self.mgr.list_connectors(status="active")
        paused = self.mgr.list_connectors(status="paused")
        assert len(active) == 0
        assert len(paused) == 1

    def test_update_connector(self):
        c = self.mgr.create_connector("Test", "webhook_in")
        assert self.mgr.update_connector(c.connector_id, name="Updated", endpoint="https://new.com")
        found = self.mgr.get_connector(c.connector_id)
        assert found.name == "Updated"
        assert found.endpoint == "https://new.com"

    def test_update_connector_not_found(self):
        assert self.mgr.update_connector("nonexistent", name="X") is False

    def test_update_connector_status(self):
        c = self.mgr.create_connector("Test", "webhook_in")
        self.mgr.update_connector(c.connector_id, status="error")
        found = self.mgr.get_connector(c.connector_id)
        assert found.status == ConnectorStatus.ERROR

    def test_update_connector_type(self):
        c = self.mgr.create_connector("Test", "webhook_in")
        self.mgr.update_connector(c.connector_id, type="rest_api")
        found = self.mgr.get_connector(c.connector_id)
        assert found.type == ConnectorType.REST_API

    def test_delete_connector(self):
        c = self.mgr.create_connector("Test", "webhook_in")
        assert self.mgr.delete_connector(c.connector_id) is True
        assert self.mgr.get_connector(c.connector_id) is None

    def test_delete_connector_not_found(self):
        assert self.mgr.delete_connector("nonexistent") is False

    def test_record_event(self):
        c = self.mgr.create_connector("Test", "webhook_in")
        ev = self.mgr.record_event(c.connector_id, payload={"key": "val"})
        assert ev.connector_id == c.connector_id
        assert ev.event_id.startswith("evt-")
        found = self.mgr.get_connector(c.connector_id)
        assert found.trigger_count == 1

    def test_record_event_max_events(self):
        c = self.mgr.create_connector("Test", "webhook_in")
        self.mgr._max_events = 5
        for i in range(10):
            self.mgr.record_event(c.connector_id)
        events = self.mgr.get_events(c.connector_id)
        assert len(events) <= 5

    def test_get_events_filter(self):
        c1 = self.mgr.create_connector("A", "webhook_in")
        c2 = self.mgr.create_connector("B", "webhook_in")
        self.mgr.record_event(c1.connector_id)
        self.mgr.record_event(c2.connector_id)
        self.mgr.record_event(c1.connector_id)
        events_a = self.mgr.get_events(c1.connector_id)
        events_b = self.mgr.get_events(c2.connector_id)
        assert len(events_a) == 2
        assert len(events_b) == 1

    def test_send_webhook_not_found(self):
        result = self.mgr.send_webhook("nonexistent", {"key": "val"})
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_send_webhook_inactive(self):
        c = self.mgr.create_connector("Test", "webhook_out")
        self.mgr.update_connector(c.connector_id, status="paused")
        result = self.mgr.send_webhook(c.connector_id, {"key": "val"})
        assert result["success"] is False
        assert "paused" in result["error"]

    def test_send_webhook_no_endpoint(self):
        c = self.mgr.create_connector("Test", "webhook_out", endpoint="")
        result = self.mgr.send_webhook(c.connector_id, {"key": "val"})
        assert result["success"] is False
        assert "No endpoint" in result["error"]

    def test_send_webhook_success(self):
        c = self.mgr.create_connector("Test", "webhook_out", endpoint="https://example.com/hook")
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_open.return_value.__enter__ = MagicMock(return_value=mock_resp)
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            result = self.mgr.send_webhook(c.connector_id, {"key": "val"})
            assert result["success"] is True
            assert result["status_code"] == 200

    def test_send_webhook_with_signature(self):
        c = self.mgr.create_connector(
            "Test", "webhook_out",
            endpoint="https://example.com/hook",
            secret="my_secret",
        )
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_open.return_value.__enter__ = MagicMock(return_value=mock_resp)
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            result = self.mgr.send_webhook(c.connector_id, {"key": "val"})
            assert result["success"] is True
            # Verify HMAC signature was added
            call_args = mock_open.call_args
            req = call_args[0][0]
            # urllib normalizes header names to title-case
            assert any(k.lower() == "x-signature" for k in req.headers)

    def test_send_webhook_failure(self):
        c = self.mgr.create_connector("Test", "webhook_out", endpoint="https://example.com/hook")
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = self.mgr.send_webhook(c.connector_id, {"key": "val"})
            assert result["success"] is False
            assert "Connection refused" in result["error"]
            found = self.mgr.get_connector(c.connector_id)
            assert found.error_count == 1

    def test_stats(self):
        self.mgr.create_connector("A", "webhook_in")
        self.mgr.create_connector("B", "rest_api")
        c3 = self.mgr.create_connector("C", "webhook_in")
        self.mgr.update_connector(c3.connector_id, status="paused")
        self.mgr.record_event(c3.connector_id)
        stats = self.mgr.stats()
        assert stats["total_connectors"] == 3
        assert stats["active"] == 2
        assert stats["total_events"] == 1
        assert stats["by_type"]["webhook_in"] == 2
        assert stats["by_type"]["rest_api"] == 1

    def test_create_connector_with_all_fields(self):
        c = self.mgr.create_connector(
            "Full",
            "custom",
            endpoint="https://x.com",
            secret="s",
            headers={"H": "V"},
            config={"C": "D"},
            description="desc",
            tags=["t1", "t2"],
        )
        assert c.endpoint == "https://x.com"
        assert c.secret == "s"
        assert c.headers == {"H": "V"}
        assert c.config == {"C": "D"}
        assert c.description == "desc"
        assert c.tags == ["t1", "t2"]
