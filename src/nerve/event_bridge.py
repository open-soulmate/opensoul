"""Event Bridge — automatically publishes events to the Nerve bus when
organ actions occur.  Called inline from API endpoints so every important
action becomes a bus event that the Activity feed can display.

Usage in any API module:
    from src.nerve.event_bridge import emit
    await emit("vein", "file_uploaded", "📄 File uploaded: report.pdf", {"file_id": "..."})
"""

from __future__ import annotations

import time
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE = "http://127.0.0.1:8090"

# Organ emoji map
ORGAN_EMOJI: dict[str, str] = {
    "soul": "🧠", "cortex": "🧩", "nerve": "⚡", "vein": "🩸",
    "sense": "👁", "will": "✨", "immune": "🛡", "vital": "📊",
    "marrow": "🦴", "gland": "🧪", "gene": "🧬", "echo": "🔊",
    "mirror": "🪞", "link": "🔗", "hippo": "🧠", "reflex": "⚡",
    "heredity": "🔗", "pulse": "💓", "nest": "🏠", "limb": "💪",
    "voice": "🎤", "vision": "🎨", "mind": "💭", "mate": "👤",
    "soma": "🤖", "trajectory": "📊",
}


async def emit(
    organ: str,
    event_type: str,
    summary: str,
    detail: dict[str, Any] | None = None,
    *,
    _client: httpx.AsyncClient | None = None,
) -> bool:
    """Publish an event to both the Nerve bus and the in-memory event buffer.

    Returns True if at least one publish succeeded.  Never raises —
    failures are logged and swallowed so the caller's main path is
    unaffected.
    """
    emoji = ORGAN_EMOJI.get(organ, "🔔")
    topic = f"organ.{organ}.{event_type}"
    payload = {
        "organ": organ,
        "emoji": emoji,
        "type": event_type,
        "summary": summary,
        "detail": detail or {},
        "timestamp": time.time(),
    }

    ok = False

    own_client = _client is None
    if own_client:
        _client = httpx.AsyncClient(timeout=3.0)

    try:
        # 1) Publish to Nerve event bus
        try:
            r = await _client.post(
                f"{_BASE}/api/nerve/publish",
                json={"topic": topic, "data": payload, "source": organ},
            )
            if r.status_code == 200:
                ok = True
        except Exception as exc:
            logger.debug("Nerve publish failed for %s: %s", topic, exc)

        # 2) Append to the event-stream buffer so Activity page picks it up
        try:
            from src.api.event_stream import push_event  # type: ignore[attr-defined]
            push_event({
                "organ": organ,
                "emoji": emoji,
                "type": event_type,
                "summary": summary,
                "detail": detail or {},
                "timestamp": time.time(),
            })
        except (ImportError, AttributeError):
            # push_event may not exist yet — that's fine
            pass

        # 2b) Record to persistent timeline
        try:
            from src.timeline.store import TimelineStore
            if not hasattr(emit, "_timeline"):
                emit._timeline = TimelineStore()  # type: ignore[attr-defined]
            emit._timeline.record({
                "id": f"evt_{int(time.time()*1000)}_{organ}_{event_type}",
                "organ": organ,
                "emoji": emoji,
                "type": event_type,
                "summary": summary,
                "detail": detail or {},
                "timestamp": time.time(),
                "collected_at": time.time(),
            })
        except Exception:
            pass

        # 3) Push to Notification Center
        try:
            from src.api.notifications import push_notification
            # Only notify for important events (errors, warnings, key actions)
            level = "info"
            if event_type in ("error", "alert", "failure"):
                level = "error"
            elif event_type in ("warning", "degraded"):
                level = "warning"
            elif event_type in ("completed", "success", "uploaded"):
                level = "success"
            push_notification(
                source="event_bridge",
                title=f"{emoji} {organ.upper()}",
                body=summary,
                level=level,
                organ=organ,
                emoji=emoji,
                action_url=f"/{organ}",
                metadata={"event_type": event_type, "detail": detail},
            )
        except (ImportError, AttributeError):
            pass

    finally:
        if own_client:
            await _client.aclose()

    return ok


def push_event(event: dict[str, Any]) -> None:
    """Append an event to the in-memory ring buffer AND publish to Nerve bus.

    This is the synchronous convenience wrapper used by API endpoints.
    For Nerve bus delivery it fires-and-forgets an async task.
    """
    try:
        from src.api.event_stream import _event_buffer
        event.setdefault("id", f"evt_{int(time.time()*1000)}")
        event.setdefault("collected_at", time.time())
        _event_buffer.append(event)
    except ImportError:
        pass

    # Also record to persistent timeline
    try:
        from src.timeline.store import TimelineStore
        if not hasattr(push_event, "_timeline"):
            push_event._timeline = TimelineStore()  # type: ignore[attr-defined]
        push_event._timeline.record(event)  # type: ignore[attr-defined]
    except Exception:
        pass

    # Also publish to Nerve bus (fire-and-forget)
    try:
        import asyncio
        organ = event.get("organ", "unknown")
        event_type = event.get("type", "unknown")
        topic = f"organ.{organ}.{event_type}"
        loop = asyncio.get_running_loop()
        loop.create_task(_publish_to_nerve(topic, event, organ))
    except RuntimeError:
        pass  # No event loop running — skip Nerve


async def _publish_to_nerve(topic: str, payload: dict, source: str) -> None:
    """Background task to publish to Nerve bus."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(
                f"{_BASE}/api/nerve/publish",
                json={"topic": topic, "data": payload, "source": source},
            )
    except Exception:
        pass
