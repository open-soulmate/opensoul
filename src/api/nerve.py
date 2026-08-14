"""OpenNerve API — 神经系统：事件总线、消息分发、节点管理。"""

import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter()

# ── In-Memory Event Bus (NATS-lite, no external dependency) ────


class EventBus:
    """Lightweight in-process event bus with topic-based pub/sub."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._events: list[dict[str, Any]] = []
        self._nodes: dict[str, dict[str, Any]] = {}
        self._max_events = 5000

    def publish(self, topic: str, data: dict[str, Any], source: str = "") -> dict[str, Any]:
        event = {
            "id": f"evt_{len(self._events) + 1}_{int(time.time())}",
            "topic": topic,
            "data": data,
            "source": source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "delivered_to": [],
        }
        # Deliver to matching subscribers
        for pattern, subs in self._subscribers.items():
            if self._topic_matches(pattern, topic):
                for sub in subs:
                    event["delivered_to"].append(sub["id"])
                    sub["last_delivery"] = event["timestamp"]
                    sub["delivery_count"] = sub.get("delivery_count", 0) + 1

        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]
        return event

    def subscribe(self, subscriber_id: str, topic_pattern: str, callback_url: str = "") -> dict[str, Any]:
        sub = {
            "id": subscriber_id,
            "topic_pattern": topic_pattern,
            "callback_url": callback_url,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "delivery_count": 0,
            "last_delivery": None,
        }
        self._subscribers[topic_pattern].append(sub)
        return sub

    def unsubscribe(self, subscriber_id: str) -> bool:
        for pattern in list(self._subscribers.keys()):
            subs = self._subscribers[pattern]
            before = len(subs)
            self._subscribers[pattern] = [s for s in subs if s["id"] != subscriber_id]
            if len(self._subscribers[pattern]) < before:
                if not self._subscribers[pattern]:
                    del self._subscribers[pattern]
                return True
        return False

    def register_node(self, node_id: str, node_type: str, metadata: dict | None = None) -> dict[str, Any]:
        node = {
            "node_id": node_id,
            "node_type": node_type,
            "status": "online",
            "metadata": metadata or {},
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "event_count": 0,
        }
        self._nodes[node_id] = node
        return node

    def heartbeat(self, node_id: str) -> bool:
        if node_id not in self._nodes:
            return False
        self._nodes[node_id]["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
        self._nodes[node_id]["status"] = "online"
        return True

    def remove_node(self, node_id: str) -> bool:
        return self._nodes.pop(node_id, None) is not None

    def get_events(self, topic: str | None = None, limit: int = 100, since: str | None = None) -> list[dict]:
        events = self._events
        if topic:
            events = [e for e in events if self._topic_matches(topic, e["topic"])]
        if since:
            events = [e for e in events if e["timestamp"] > since]
        return events[-limit:]

    def stats(self) -> dict[str, Any]:
        online = sum(1 for n in self._nodes.values() if n["status"] == "online")
        total_subs = sum(len(s) for s in self._subscribers.values())
        return {
            "total_events": len(self._events),
            "total_nodes": len(self._nodes),
            "online_nodes": online,
            "total_subscriptions": total_subs,
            "topics": list(set(e["topic"] for e in self._events[-100:])),
        }

    @staticmethod
    def _topic_matches(pattern: str, topic: str) -> bool:
        if pattern == "*" or pattern == topic:
            return True
        # Simple wildcard: "soma.*" matches "soma.abc.heartbeat"
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return topic.startswith(prefix + ".")
        if ".*." in pattern:
            parts = pattern.split(".*")
            return all(part in topic for part in parts if part)
        return False


bus = EventBus()


# ── Request Schemas ────────────────────────────────────────────

class PublishRequest(BaseModel):
    topic: str
    data: dict[str, Any] = Field(default_factory=dict)
    source: str = ""


class SubscribeRequest(BaseModel):
    subscriber_id: str
    topic_pattern: str
    callback_url: str = ""


class NodeRegisterRequest(BaseModel):
    node_id: str
    node_type: str = "soma"
    metadata: dict[str, Any] = Field(default_factory=dict)


class HeartbeatRequest(BaseModel):
    node_id: str


# ── Event Endpoints ────────────────────────────────────────────

@router.post("/publish")
async def publish_event(req: PublishRequest):
    """Publish an event to the bus."""
    event = bus.publish(req.topic, req.data, req.source)
    return event


@router.get("/events")
async def list_events(
    topic: str = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    since: str = Query(default=None),
):
    """Query events from the bus."""
    return {"events": bus.get_events(topic=topic, limit=limit, since=since)}


# ── Subscription Endpoints ─────────────────────────────────────

@router.post("/subscribe")
async def subscribe(req: SubscribeRequest):
    """Subscribe to a topic pattern."""
    sub = bus.subscribe(req.subscriber_id, req.topic_pattern, req.callback_url)
    return sub


@router.delete("/subscribe/{subscriber_id}")
async def unsubscribe(subscriber_id: str):
    """Unsubscribe from all topics."""
    if not bus.unsubscribe(subscriber_id):
        raise HTTPException(404, "Subscriber not found")
    return {"status": "ok", "subscriber_id": subscriber_id}


@router.get("/subscriptions")
async def list_subscriptions():
    """List all active subscriptions."""
    subs = []
    for pattern, sub_list in bus._subscribers.items():
        for s in sub_list:
            subs.append({**s, "topic_pattern": pattern})
    return {"subscriptions": subs, "count": len(subs)}


# ── Node Management ────────────────────────────────────────────

@router.post("/nodes/register")
async def register_node(req: NodeRegisterRequest):
    """Register a node (Soma/Sense/etc.) with the event bus."""
    node = bus.register_node(req.node_id, req.node_type, req.metadata)
    return node


@router.post("/nodes/heartbeat")
async def node_heartbeat(req: HeartbeatRequest):
    """Send heartbeat for a node."""
    if not bus.heartbeat(req.node_id):
        raise HTTPException(404, f"Node '{req.node_id}' not registered")
    return {"node_id": req.node_id, "status": "online"}


@router.delete("/nodes/{node_id}")
async def remove_node(node_id: str):
    """Remove a node from the bus."""
    if not bus.remove_node(node_id):
        raise HTTPException(404, "Node not found")
    return {"status": "ok", "node_id": node_id}


@router.get("/nodes")
async def list_nodes():
    """List all registered nodes."""
    nodes = list(bus._nodes.values())
    return {"nodes": nodes, "count": len(nodes)}


# ── Health / Stats ─────────────────────────────────────────────

@router.get("/health")
async def nerve_health():
    """OpenNerve health check."""
    return {
        "status": "ok",
        "component": "OpenNerve",
        "bus": bus.stats(),
    }


@router.get("/stats")
async def nerve_stats():
    """Get OpenNerve statistics."""
    return bus.stats()
