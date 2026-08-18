"""OpenTimeline API — Persistent cross-component event timeline.

Aggregates events from all organs into a searchable, filterable timeline
with persistent SQLite storage. Complements the in-memory event stream
with full history, statistics, and analytics.
"""

import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.timeline.store import TimelineStore

router = APIRouter()

# ── Singleton ──────────────────────────────────────────────
store = TimelineStore()


# ── Request Schemas ────────────────────────────────────────


class RecordEventRequest(BaseModel):
    organ: str
    emoji: str = ""
    event_type: str
    summary: str
    detail: dict = {}


class ClearRequest(BaseModel):
    older_than_days: int | None = None


# ── Event Recording ────────────────────────────────────────


@router.post("/record")
async def record_event(req: RecordEventRequest):
    """Record an event to the persistent timeline."""
    import uuid

    event = {
        "id": f"tl_{uuid.uuid4().hex[:12]}",
        "organ": req.organ,
        "emoji": req.emoji,
        "type": req.event_type,
        "summary": req.summary,
        "detail": req.detail,
        "timestamp": time.time(),
        "collected_at": time.time(),
    }
    ok = store.record(event)
    return {"status": "ok" if ok else "duplicate", "event_id": event["id"]}


# ── Event Querying ─────────────────────────────────────────


@router.get("/events")
async def list_events(
    organ: str = Query(default=None, description="Filter by organ name"),
    event_type: str = Query(default=None, description="Filter by event type"),
    search: str = Query(default=None, description="Search in summary and detail"),
    since: float = Query(default=None, description="Events after this timestamp"),
    until: float = Query(default=None, description="Events before this timestamp"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """Query timeline events with rich filtering."""
    events = store.query(
        organ=organ,
        event_type=event_type,
        search=search,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
        "limit": limit,
        "offset": offset,
    }


@router.get("/events/{event_id}")
async def get_event(event_id: str):
    """Get a single event by ID."""
    event = store.get_event(event_id)
    if not event:
        raise HTTPException(404, "Event not found")
    return event.to_dict()


@router.delete("/events/{event_id}")
async def delete_event(event_id: str):
    """Delete a single event."""
    if not store.delete_event(event_id):
        raise HTTPException(404, "Event not found")
    return {"status": "ok", "event_id": event_id}


# ── Bulk Operations ────────────────────────────────────────


@router.post("/clear")
async def clear_events(req: ClearRequest | None = None):
    """Clear events. Optionally only clear events older than N days."""
    older = req.older_than_days if req else None
    count = store.clear(older_than_days=older)
    return {"status": "ok", "cleared": count}


# ── Statistics ─────────────────────────────────────────────


@router.get("/stats")
async def timeline_stats():
    """Get comprehensive timeline statistics."""
    return store.stats()


@router.get("/organs")
async def list_organs():
    """List all organs with event counts."""
    return {"organs": store.organs()}


@router.get("/types")
async def list_event_types():
    """List all event types with counts."""
    return {"types": store.event_types()}


# ── Sync from Event Stream ─────────────────────────────────


@router.post("/sync")
async def sync_from_stream():
    """Sync events from the in-memory event stream buffer to persistent timeline.

    This captures events that were pushed via push_event() but not yet
    recorded in the timeline database.
    """
    try:
        from src.api.event_stream import _event_buffer

        synced = 0
        for event in _event_buffer:
            if store.record(event):
                synced += 1
        return {"status": "ok", "synced": synced, "buffer_size": len(_event_buffer)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def timeline_health():
    """OpenTimeline health check."""
    stats = store.stats()
    return {
        "status": "ok",
        "component": "OpenTimeline",
        "total_events": stats["total_events"],
        "recent_24h": stats["recent_24h"],
    }
