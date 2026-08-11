"""Event data models for OpenNerve messaging."""

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class EventBase(BaseModel):
    """Base class for all Nerve events."""

    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = ""

    def serialize(self) -> bytes:
        return self.model_dump_json().encode()

    @classmethod
    def deserialize(cls, data: bytes) -> "EventBase":
        return cls.model_validate_json(data)


class HeartbeatEvent(EventBase):
    event_type: Literal["heartbeat"] = "heartbeat"
    node_id: str
    status: str = "alive"
    uptime_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class DataReportEvent(EventBase):
    event_type: Literal["data_report"] = "data_report"
    node_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskAssignEvent(EventBase):
    event_type: Literal["task_assign"] = "task_assign"
    node_id: str
    task_id: str
    task_type: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0


class KnowledgeUpdateEvent(EventBase):
    event_type: Literal["knowledge_update"] = "knowledge_update"
    knowledge_id: str
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    operation: str = "upsert"


class SystemEvent(EventBase):
    event_type: Literal["system_event"] = "system_event"
    level: str = "info"
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


_EVENT_REGISTRY: dict[str, type[EventBase]] = {
    "heartbeat": HeartbeatEvent,
    "data_report": DataReportEvent,
    "task_assign": TaskAssignEvent,
    "knowledge_update": KnowledgeUpdateEvent,
    "system_event": SystemEvent,
}


def deserialize_event(data: bytes) -> EventBase:
    """Deserialize raw bytes into the appropriate Event subclass."""
    obj = json.loads(data)
    event_type = obj.get("event_type")
    cls = _EVENT_REGISTRY.get(event_type, EventBase)
    return cls.model_validate(obj)
