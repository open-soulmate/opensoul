"""Unit tests for echo/dispatcher.py — multi-channel message dispatcher."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.echo.dispatcher import (
    Channel,
    ChannelConfig,
    Message,
    MessageDispatcher,
    SendResult,
)


class TestChannel:
    def test_all_values(self):
        assert Channel.WEBHOOK == "webhook"
        assert Channel.EMAIL == "email"
        assert Channel.DINGTALK == "dingtalk"
        assert Channel.WECHAT_WORK == "wechat_work"
        assert Channel.TELEGRAM == "telegram"
        assert Channel.FEISHU == "feishu"
        assert Channel.SLACK == "slack"
        assert Channel.DISCORD == "discord"
        assert Channel.CONSOLE == "console"

    def test_channel_count(self):
        assert len(Channel) == 9


class TestChannelConfig:
    def test_defaults(self):
        cfg = ChannelConfig(channel=Channel.CONSOLE)
        assert cfg.enabled is True
        assert cfg.endpoint == ""
        assert cfg.token == ""
        assert cfg.extra == {}

    def test_custom(self):
        cfg = ChannelConfig(
            channel=Channel.WEBHOOK,
            enabled=False,
            endpoint="https://example.com",
            token="tok",
            extra={"key": "val"},
        )
        assert cfg.enabled is False
        assert cfg.endpoint == "https://example.com"
        assert cfg.token == "tok"
        assert cfg.extra == {"key": "val"}


class TestMessage:
    def test_defaults(self):
        msg = Message(
            msg_id="m1", channel=Channel.CONSOLE, title="T", content="C", timestamp=1.0
        )
        assert msg.status == "pending"
        assert msg.error == ""
        assert msg.target == ""
        assert msg.priority == 5


class TestSendResult:
    def test_basic(self):
        r = SendResult(success=True, msg_id="m1", channel="console")
        assert r.success is True
        assert r.error == ""


class TestMessageDispatcher:
    def setup_method(self):
        self.d = MessageDispatcher()

    def test_init_has_console_channel(self):
        channels = self.d.list_channels()
        assert len(channels) == 1
        assert channels[0]["channel"] == "console"
        assert channels[0]["enabled"] is True

    def test_configure_channel(self):
        self.d.configure_channel(
            Channel.WEBHOOK,
            endpoint="https://example.com",
            token="tok",
            enabled=True,
        )
        channels = self.d.list_channels()
        webhook = [c for c in channels if c["channel"] == "webhook"]
        assert len(webhook) == 1
        assert webhook[0]["has_endpoint"] is True
        assert webhook[0]["has_token"] is True

    def test_send_console(self):
        result = self.d.send(Channel.CONSOLE, "Title", "Body")
        assert result.success is True
        assert result.channel == "console"

    def test_send_unconfigured_channel(self):
        result = self.d.send(Channel.WEBHOOK, "Title", "Body")
        assert result.success is False
        assert "not configured" in result.error

    def test_send_disabled_channel(self):
        self.d.configure_channel(Channel.WEBHOOK, endpoint="https://x.com", enabled=False)
        result = self.d.send(Channel.WEBHOOK, "Title", "Body")
        assert result.success is False
        assert "disabled" in result.error

    def test_send_all(self):
        self.d.configure_channel(Channel.WEBHOOK, endpoint="https://example.com")
        results = self.d.send_all("Title", "Body")
        # console + webhook
        assert len(results) == 2

    def test_history(self):
        self.d.send(Channel.CONSOLE, "T1", "C1")
        self.d.send(Channel.CONSOLE, "T2", "C2")
        history = self.d.history(limit=10)
        assert len(history) == 2
        assert history[0]["title"] == "T2"  # reversed order

    def test_history_filter_by_channel(self):
        self.d.configure_channel(Channel.WEBHOOK, endpoint="https://x.com")
        self.d.send(Channel.CONSOLE, "T1", "C1")
        self.d.send(Channel.WEBHOOK, "T2", "C2")
        history = self.d.history(channel=Channel.CONSOLE)
        assert len(history) == 1
        assert history[0]["title"] == "T1"

    def test_stats(self):
        self.d.send(Channel.CONSOLE, "T1", "C1")
        self.d.configure_channel(Channel.WEBHOOK, endpoint="https://x.com")
        self.d.send(Channel.WEBHOOK, "T2", "C2")
        stats = self.d.stats()
        assert stats["total_messages"] == 2
        assert stats["sent"] >= 1  # console succeeds
        assert stats["channels_configured"] == 2
        assert stats["channels_enabled"] == 2

    def test_stats_disabled_channel(self):
        self.d.configure_channel(Channel.WEBHOOK, endpoint="https://x.com", enabled=False)
        stats = self.d.stats()
        assert stats["channels_enabled"] == 1  # only console

    def test_message_id_increment(self):
        r1 = self.d.send(Channel.CONSOLE, "T1", "C1")
        r2 = self.d.send(Channel.CONSOLE, "T2", "C2")
        assert r1.msg_id != r2.msg_id

    def test_priority_in_message(self):
        result = self.d.send(Channel.CONSOLE, "T", "C", priority=1)
        assert result.success is True
        history = self.d.history()
        assert history[0]["priority"] == 1


class TestWebhookDispatch:
    def test_webhook_success(self):
        d = MessageDispatcher()
        d.configure_channel(Channel.WEBHOOK, endpoint="https://example.com/hook")
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_open.return_value.__enter__ = MagicMock(return_value=mock_resp)
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            result = d.send(Channel.WEBHOOK, "Title", "Body")
            assert result.success is True

    def test_webhook_failure(self):
        d = MessageDispatcher()
        d.configure_channel(Channel.WEBHOOK, endpoint="https://example.com/hook")
        with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
            result = d.send(Channel.WEBHOOK, "Title", "Body")
            assert result.success is False
            assert "Network error" in result.error


class TestDingtalkDispatch:
    def test_dingtalk_success(self):
        d = MessageDispatcher()
        d.configure_channel(Channel.DINGTALK, endpoint="https://oapi.dingtalk.com/robot/send")
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_open.return_value.__enter__ = MagicMock(return_value=mock_resp)
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            result = d.send(Channel.DINGTALK, "Title", "Body")
            assert result.success is True

    def test_dingtalk_failure(self):
        d = MessageDispatcher()
        d.configure_channel(Channel.DINGTALK, endpoint="https://oapi.dingtalk.com/robot/send")
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = d.send(Channel.DINGTALK, "Title", "Body")
            assert result.success is False


class TestFeishuDispatch:
    def test_feishu_success(self):
        d = MessageDispatcher()
        d.configure_channel(Channel.FEISHU, endpoint="https://open.feishu.cn/open-apis/bot/v2/hook")
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_open.return_value.__enter__ = MagicMock(return_value=mock_resp)
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            result = d.send(Channel.FEISHU, "Title", "Body")
            assert result.success is True


class TestTelegramDispatch:
    def test_telegram_missing_token(self):
        d = MessageDispatcher()
        d.configure_channel(Channel.TELEGRAM, token="", endpoint="")
        result = d.send(Channel.TELEGRAM, "Title", "Body", target="")
        assert result.success is False
        assert "Missing token" in result.error

    def test_telegram_success(self):
        d = MessageDispatcher()
        d.configure_channel(Channel.TELEGRAM, token="bot123:ABC")
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"ok": True}).encode()
            mock_open.return_value.__enter__ = MagicMock(return_value=mock_resp)
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            result = d.send(Channel.TELEGRAM, "Title", "Body", target="12345")
            assert result.success is True


class TestEmailDispatch:
    def test_email_missing_host(self):
        d = MessageDispatcher()
        d.configure_channel(Channel.EMAIL, endpoint="", extra={})
        result = d.send(Channel.EMAIL, "Title", "Body", target="user@example.com")
        assert result.success is False
        assert "Missing smtp_host" in result.error

    def test_email_missing_recipient(self):
        d = MessageDispatcher()
        d.configure_channel(Channel.EMAIL, endpoint="smtp.example.com", extra={})
        result = d.send(Channel.EMAIL, "Title", "Body", target="")
        assert result.success is False
        assert "Missing" in result.error

    def test_email_success_ssl(self):
        d = MessageDispatcher()
        d.configure_channel(
            Channel.EMAIL,
            endpoint="smtp.example.com",
            extra={"username": "user", "password": "pass", "from": "a@b.com", "use_ssl": True},
        )
        with patch("smtplib.SMTP_SSL") as mock_smtp:
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
            result = d.send(Channel.EMAIL, "Title", "Body", target="dest@b.com")
            assert result.success is True

    def test_email_success_starttls(self):
        d = MessageDispatcher()
        d.configure_channel(
            Channel.EMAIL,
            endpoint="smtp.example.com",
            extra={"username": "user", "password": "pass", "from": "a@b.com", "smtp_port": 587},
        )
        with patch("smtplib.SMTP") as mock_smtp:
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
            result = d.send(Channel.EMAIL, "Title", "Body", target="dest@b.com")
            assert result.success is True


class TestWechatWorkDispatch:
    def test_wechat_missing_url(self):
        d = MessageDispatcher()
        d.configure_channel(Channel.WECHAT_WORK, endpoint="")
        result = d.send(Channel.WECHAT_WORK, "Title", "Body")
        assert result.success is False
        assert "Missing webhook URL" in result.error

    def test_wechat_success(self):
        d = MessageDispatcher()
        d.configure_channel(Channel.WECHAT_WORK, endpoint="https://qyapi.weixin.qq.com/cgi-bin/webhook/send")
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"errcode": 0, "errmsg": "ok"}).encode()
            mock_open.return_value.__enter__ = MagicMock(return_value=mock_resp)
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            result = d.send(Channel.WECHAT_WORK, "Title", "Body")
            assert result.success is True

    def test_wechat_api_error(self):
        d = MessageDispatcher()
        d.configure_channel(Channel.WECHAT_WORK, endpoint="https://qyapi.weixin.qq.com/cgi-bin/webhook/send")
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"errcode": 93000, "errmsg": "invalid webhook"}).encode()
            mock_open.return_value.__enter__ = MagicMock(return_value=mock_resp)
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            result = d.send(Channel.WECHAT_WORK, "Title", "Body")
            assert result.success is False
            assert "invalid webhook" in result.error
