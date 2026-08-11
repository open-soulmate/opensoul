"""NATS client for OpenNerve messaging."""

import asyncio
import logging
from typing import Any, Callable, Awaitable

import nats
from nats.aio.client import Client as NATSClient
from nats.aio.msg import Msg

from .events import EventBase, deserialize_event

logger = logging.getLogger(__name__)

EventCallback = Callable[[EventBase], Awaitable[None]]


class NerveClient:
    """Async NATS client with auto-reconnect and event abstraction."""

    def __init__(
        self,
        servers: str | list[str] = "nats://localhost:4222",
        name: str = "nerve-client",
        reconnect_time_wait: float = 2.0,
        max_reconnect_attempts: int = -1,
    ) -> None:
        if isinstance(servers, str):
            servers = [servers]
        self._servers = servers
        self._name = name
        self._reconnect_time_wait = reconnect_time_wait
        self._max_reconnect_attempts = max_reconnect_attempts
        self._nc: NATSClient | None = None
        self._subscriptions: list[tuple[str, Any]] = []

    @property
    def is_connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected

    async def connect(self) -> None:
        """Connect to NATS server(s) with auto-reconnect."""
        self._nc = await nats.connect(
            servers=self._servers,
            name=self._name,
            reconnect_time_wait=self._reconnect_time_wait,
            max_reconnect_attempts=self._max_reconnect_attempts,
            disconnected_cb=self._on_disconnected,
            reconnected_cb=self._on_reconnected,
            error_cb=self._on_error,
        )
        logger.info("Connected to NATS: %s", self._nc.connected_url)

    async def close(self) -> None:
        """Drain and close the connection."""
        if self._nc:
            for _, sub in self._subscriptions:
                await sub.drain()
            self._subscriptions.clear()
            await self._nc.drain()
            self._nc = None

    async def publish(self, topic: str, data: EventBase | bytes | dict[str, Any]) -> None:
        """Publish an event to a topic."""
        self._ensure_connected()
        if isinstance(data, EventBase):
            payload = data.serialize()
        elif isinstance(data, dict):
            import json
            payload = json.dumps(data).encode()
        else:
            payload = data
        await self._nc.publish(topic, payload)

    async def subscribe(self, topic: str, callback: EventCallback) -> None:
        """Subscribe to a topic with an async callback receiving deserialized events."""
        self._ensure_connected()

        async def _handler(msg: Msg) -> None:
            try:
                event = deserialize_event(msg.data)
                await callback(event)
            except Exception:
                logger.exception("Error handling message on %s", msg.subject)

        sub = await self._nc.subscribe(topic, cb=_handler)
        self._subscriptions.append((topic, sub))

    async def request(
        self,
        topic: str,
        data: EventBase | bytes | dict[str, Any],
        timeout: float = 5.0,
    ) -> bytes:
        """Send a request and wait for a response."""
        self._ensure_connected()
        if isinstance(data, EventBase):
            payload = data.serialize()
        elif isinstance(data, dict):
            import json
            payload = json.dumps(data).encode()
        else:
            payload = data
        response = await self._nc.request(topic, payload, timeout=timeout)
        return response.data

    def _ensure_connected(self) -> None:
        if not self.is_connected:
            raise RuntimeError("Not connected to NATS. Call connect() first.")

    async def _on_disconnected(self) -> None:
        logger.warning("Disconnected from NATS")

    async def _on_reconnected(self) -> None:
        logger.info("Reconnected to NATS: %s", self._nc.connected_url)

    async def _on_error(self, e: Exception) -> None:
        logger.error("NATS error: %s", e)
