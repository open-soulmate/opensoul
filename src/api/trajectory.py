"""OpenTrajectory API — Agent execution trace, replay, and fork endpoints."""

import json
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.trajectory.store import trajectory_store, TrajectoryEvent, EventType

router = APIRouter()


# ── Request Schemas ──────────────────────────────────────────

class SessionCreateRequest(BaseModel):
    agent_id: str = ""
    task_description: str = ""
    tags: list[str] = []


class EventRecordRequest(BaseModel):
    event_type: str = EventType.CUSTOM.value
    agent_id: str = ""
    content: str = ""
    metadata: dict = {}
    parent_event_id: str | None = None
    token_usage: int = 0
    duration_ms: float = 0.0
    status: str = "ok"


class ForkRequest(BaseModel):
    fork_point_event_id: str
    new_agent_id: str = ""


class BatchEventRequest(BaseModel):
    events: list[EventRecordRequest]


# ── Health ───────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"status": "ok", "component": "trajectory"}


# ── Stats ────────────────────────────────────────────────────

@router.get("/stats")
async def stats():
    """Get trajectory system statistics."""
    return await trajectory_store.get_stats()


# ── Sessions ─────────────────────────────────────────────────

@router.post("/sessions")
async def create_session(req: SessionCreateRequest):
    """Create a new trajectory session."""
    session = await trajectory_store.create_session(
        agent_id=req.agent_id,
        task_description=req.task_description,
        tags=req.tags,
    )
    return session.to_dict()


@router.get("/sessions")
async def list_sessions(
    agent_id: str = Query(default=""),
    status: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List trajectory sessions with optional filters."""
    sessions = await trajectory_store.list_sessions(
        agent_id=agent_id, status=status, limit=limit, offset=offset,
    )
    total = await trajectory_store.count_sessions()
    return {
        "sessions": [s.to_dict() for s in sessions],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get a single trajectory session."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session.to_dict()


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and all its events."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    await trajectory_store.delete_session(session_id)
    return {"deleted": True, "session_id": session_id}


@router.post("/sessions/{session_id}/end")
async def end_session(session_id: str, status: str = Query(default="completed")):
    """End a running session."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    await trajectory_store.end_session(session_id, status=status)
    return {"ended": True, "session_id": session_id, "status": status}


# ── Events ───────────────────────────────────────────────────

@router.post("/sessions/{session_id}/events")
async def record_event(session_id: str, req: EventRecordRequest):
    """Record a single event in a session."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    event = TrajectoryEvent(
        session_id=session_id,
        parent_event_id=req.parent_event_id,
        event_type=req.event_type,
        agent_id=req.agent_id,
        content=req.content,
        metadata_json=json.dumps(req.metadata),
        token_usage=req.token_usage,
        duration_ms=req.duration_ms,
        status=req.status,
    )
    await trajectory_store.add_event(event)
    return event.to_dict()


@router.post("/sessions/{session_id}/events/batch")
async def record_events_batch(session_id: str, req: BatchEventRequest):
    """Record multiple events in a session at once."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    results = []
    for er in req.events:
        event = TrajectoryEvent(
            session_id=session_id,
            parent_event_id=er.parent_event_id,
            event_type=er.event_type,
            agent_id=er.agent_id,
            content=er.content,
            metadata_json=json.dumps(er.metadata),
            token_usage=er.token_usage,
            duration_ms=er.duration_ms,
            status=er.status,
        )
        await trajectory_store.add_event(event)
        results.append(event.to_dict())
    return {"recorded": len(results), "events": results}


@router.get("/sessions/{session_id}/events")
async def get_events(session_id: str, limit: int = Query(default=500, ge=1, le=2000)):
    """Get all events for a session (ordered by time)."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    events = await trajectory_store.get_events(session_id, limit=limit)
    return {
        "session_id": session_id,
        "events": [e.to_dict() for e in events],
        "count": len(events),
    }


@router.get("/events/{event_id}")
async def get_event(event_id: str):
    """Get a single event by ID."""
    event = await trajectory_store.get_event(event_id)
    if not event:
        raise HTTPException(404, "Event not found")
    return event.to_dict()


# ── Search ───────────────────────────────────────────────────

@router.get("/search")
async def search_events(
    session_id: str = Query(default=""),
    event_type: str = Query(default=""),
    keyword: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Search across trajectory events."""
    events = await trajectory_store.search_events(
        session_id=session_id, event_type=event_type,
        keyword=keyword, limit=limit,
    )
    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
        "filters": {"session_id": session_id, "event_type": event_type, "keyword": keyword},
    }


# ── Fork (Branch) ────────────────────────────────────────────

@router.post("/sessions/{session_id}/fork")
async def fork_session(session_id: str, req: ForkRequest):
    """Fork a session at a specific event — creates a new session
    with events copied up to the fork point for branching execution."""
    try:
        new_session = await trajectory_store.fork_session(
            source_session_id=session_id,
            fork_point_event_id=req.fork_point_event_id,
            new_agent_id=req.new_agent_id,
        )
        return new_session.to_dict()
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Replay ───────────────────────────────────────────────────

@router.get("/sessions/{session_id}/replay")
async def replay_session(session_id: str, from_event: str = Query(default="")):
    """Get events for replay — optionally starting from a specific event.
    Returns events in chronological order with step numbers."""
    session = await trajectory_store.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    events = await trajectory_store.get_events(session_id)

    if from_event:
        # Find the starting point
        start_idx = 0
        for i, ev in enumerate(events):
            if ev.id == from_event:
                start_idx = i
                break
        events = events[start_idx:]

    return {
        "session_id": session_id,
        "session": session.to_dict(),
        "steps": [
            {
                "step": i + 1,
                "event_id": ev.id,
                "type": ev.event_type,
                "agent_id": ev.agent_id,
                "content": ev.content,
                "metadata": ev.metadata,
                "token_usage": ev.token_usage,
                "duration_ms": ev.duration_ms,
                "status": ev.status,
                "timestamp": ev.created_at,
            }
            for i, ev in enumerate(events)
        ],
        "total_steps": len(events),
    }


# ── Event Types ──────────────────────────────────────────────

@router.get("/event-types")
async def list_event_types():
    """List all supported event types."""
    return {
        "types": [
            {"value": et.value, "name": et.name}
            for et in EventType
        ]
    }


# ── Analytics ──────────────────────────────────────────────

@router.get("/analytics/tools")
async def tool_analytics(limit: int = Query(default=50, ge=1, le=200)):
    """Get tool usage frequency and success rate analytics."""
    return await trajectory_store.get_tool_analytics(limit=limit)


@router.get("/analytics/agents")
async def agent_analytics(limit: int = Query(default=50, ge=1, le=200)):
    """Get per-agent performance analytics."""
    return await trajectory_store.get_agent_analytics(limit=limit)


@router.get("/analytics/event-types")
async def event_type_analytics():
    """Get event type distribution analytics."""
    return await trajectory_store.get_event_type_analytics()


@router.get("/analytics/tokens")
async def token_analytics(days: int = Query(default=30, ge=1, le=365)):
    """Get token usage over time (daily breakdown)."""
    return await trajectory_store.get_token_analytics(days=days)
