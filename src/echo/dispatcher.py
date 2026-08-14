"""Message dispatcher — multi-channel push with queue."""

from __future__ import annotations

import json
import time
import threading
import urllib.request
import urllib.error
from collections import deque
from dataclasses import dataclass, field
from enum import Enum


class Channel(str, Enum):
    WEBHOOK = "webhook"
    EMAIL = "email"
    DINGTALK = "dingtalk"
    WECHAT_WORK = "wechat_work"
    TELEGRAM = "telegram"
    FEISHU = "feishu"
    CONSOLE = "console"


@dataclass
class ChannelConfig:
    channel: Channel
    enabled: bool = True
    endpoint: str = ""
    token: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Message:
    msg_id: str
    channel: Channel
    title: str
    content: str
    timestamp: float
    status: str = "pending"  # "pending", "sent", "failed"
    error: str = ""
    target: str = ""
    priority: int = 5  # 1=highest, 10=lowest


@dataclass
class SendResult:
    success: bool
    msg_id: str
    channel: str
    error: str = ""


class MessageDispatcher:
    """Multi-channel message dispatcher with queue and retry."""

    def __init__(self):
        self._channels: dict[Channel, ChannelConfig] = {}
        self._queue: deque[Message] = deque(maxlen=10000)
        self._history: deque[Message] = deque(maxlen=1000)
        self._lock = threading.Lock()
        self._counter = 0

        # Register console channel by default
        self._channels[Channel.CONSOLE] = ChannelConfig(channel=Channel.CONSOLE, enabled=True)

    def configure_channel(self, channel: Channel, endpoint: str = "", token: str = "", enabled: bool = True, extra: dict | None = None):
        with self._lock:
            self._channels[channel] = ChannelConfig(
                channel=channel,
                enabled=enabled,
                endpoint=endpoint,
                token=token,
                extra=extra or {},
            )

    def send(self, channel: Channel, title: str, content: str, target: str = "", priority: int = 5) -> SendResult:
        """Send a message via specified channel."""
        self._counter += 1
        msg_id = f"msg_{int(time.time())}_{self._counter}"

        msg = Message(
            msg_id=msg_id,
            channel=channel,
            title=title,
            content=content,
            timestamp=time.time(),
            target=target,
            priority=priority,
        )

        config = self._channels.get(channel)
        if not config or not config.enabled:
            msg.status = "failed"
            msg.error = f"Channel '{channel.value}' not configured or disabled"
            with self._lock:
                self._history.append(msg)
            return SendResult(success=False, msg_id=msg_id, channel=channel.value, error=msg.error)

        # Dispatch
        result = self._dispatch(msg, config)

        msg.status = "sent" if result.success else "failed"
        msg.error = result.error

        with self._lock:
            self._history.append(msg)

        return result

    def send_all(self, title: str, content: str, priority: int = 5) -> list[SendResult]:
        """Broadcast to all enabled channels."""
        results = []
        for channel, config in self._channels.items():
            if config.enabled:
                results.append(self.send(channel, title, content, priority=priority))
        return results

    def _dispatch(self, msg: Message, config: ChannelConfig) -> SendResult:
        if config.channel == Channel.CONSOLE:
            print(f"[OpenEcho] {msg.title}: {msg.content}")
            return SendResult(success=True, msg_id=msg.msg_id, channel=config.channel.value)

        elif config.channel == Channel.WEBHOOK:
            return self._send_webhook(msg, config)

        elif config.channel == Channel.DINGTALK:
            return self._send_dingtalk(msg, config)

        elif config.channel == Channel.FEISHU:
            return self._send_feishu(msg, config)

        elif config.channel == Channel.TELEGRAM:
            return self._send_telegram(msg, config)

        else:
            return SendResult(
                success=False,
                msg_id=msg.msg_id,
                channel=config.channel.value,
                error=f"Channel '{config.channel.value}' not implemented yet",
            )

    def _send_webhook(self, msg: Message, config: ChannelConfig) -> SendResult:
        """Send via generic webhook (POST JSON)."""
        try:
            payload = json.dumps({
                "msg_id": msg.msg_id,
                "title": msg.title,
                "content": msg.content,
                "timestamp": msg.timestamp,
                "priority": msg.priority,
            }).encode("utf-8")

            req = urllib.request.Request(
                config.endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            if config.token:
                req.add_header("Authorization", f"Bearer {config.token}")

            with urllib.request.urlopen(req, timeout=10) as resp:
                return SendResult(success=True, msg_id=msg.msg_id, channel="webhook")

        except Exception as e:
            return SendResult(success=False, msg_id=msg.msg_id, channel="webhook", error=str(e))

    def _send_dingtalk(self, msg: Message, config: ChannelConfig) -> SendResult:
        """Send via DingTalk robot webhook."""
        try:
            payload = json.dumps({
                "msgtype": "markdown",
                "markdown": {
                    "title": msg.title,
                    "text": f"## {msg.title}\n\n{msg.content}",
                },
            }).encode("utf-8")

            req = urllib.request.Request(
                config.endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return SendResult(success=True, msg_id=msg.msg_id, channel="dingtalk")

        except Exception as e:
            return SendResult(success=False, msg_id=msg.msg_id, channel="dingtalk", error=str(e))

    def _send_feishu(self, msg: Message, config: ChannelConfig) -> SendResult:
        """Send via Feishu/Lark robot webhook."""
        try:
            payload = json.dumps({
                "msg_type": "interactive",
                "card": {
                    "header": {"title": {"tag": "plain_text", "content": msg.title}},
                    "elements": [{"tag": "markdown", "content": msg.content}],
                },
            }).encode("utf-8")

            req = urllib.request.Request(
                config.endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return SendResult(success=True, msg_id=msg.msg_id, channel="feishu")

        except Exception as e:
            return SendResult(success=False, msg_id=msg.msg_id, channel="feishu", error=str(e))

    def _send_telegram(self, msg: Message, config: ChannelConfig) -> SendResult:
        """Send via Telegram Bot API."""
        try:
            token = config.token
            chat_id = config.extra.get("chat_id", msg.target)
            if not token or not chat_id:
                return SendResult(success=False, msg_id=msg.msg_id, channel="telegram", error="Missing token or chat_id")

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps({
                "chat_id": chat_id,
                "text": f"*{msg.title}*\n\n{msg.content}",
                "parse_mode": "Markdown",
            }).encode("utf-8")

            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return SendResult(success=True, msg_id=msg.msg_id, channel="telegram")

        except Exception as e:
            return SendResult(success=False, msg_id=msg.msg_id, channel="telegram", error=str(e))

    def history(self, limit: int = 50, channel: Channel | None = None) -> list[dict]:
        with self._lock:
            entries = list(self._history)
        if channel:
            entries = [e for e in entries if e.channel == channel]
        return [
            {
                "msg_id": e.msg_id,
                "channel": e.channel.value,
                "title": e.title,
                "content": e.content[:200],
                "status": e.status,
                "error": e.error,
                "timestamp": e.timestamp,
                "priority": e.priority,
            }
            for e in sorted(entries, key=lambda x: x.timestamp, reverse=True)[:limit]
        ]

    def list_channels(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "channel": c.channel.value,
                    "enabled": c.enabled,
                    "has_endpoint": bool(c.endpoint),
                    "has_token": bool(c.token),
                }
                for c in self._channels.values()
            ]

    def stats(self) -> dict:
        with self._lock:
            total = len(self._history)
            sent = sum(1 for m in self._history if m.status == "sent")
            failed = sum(1 for m in self._history if m.status == "failed")
            return {
                "total_messages": total,
                "sent": sent,
                "failed": failed,
                "channels_configured": len(self._channels),
                "channels_enabled": sum(1 for c in self._channels.values() if c.enabled),
            }
