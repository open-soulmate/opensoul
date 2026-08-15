"""System Event Stream — aggregates recent activity from all organs into a unified timeline."""

import asyncio
import time
import uuid
from datetime import datetime, timezone
from collections import deque
from fastapi import APIRouter, Query
import httpx

router = APIRouter()

# In-memory event ring buffer (max 1000 events)
_event_buffer: deque[dict] = deque(maxlen=1000)

_BASE = "http://127.0.0.1:8090"

# ── Organ Activity Probes ──────────────────────────────────────
# Each probe fetches recent activity from an organ's API

async def _probe_vein(client: httpx.AsyncClient) -> list[dict]:
    """Get recent file operations from Vein."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/vein/stats", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            store = data.get("store", {})
            if store.get("total_files", 0) > 0:
                events.append({
                    "organ": "vein",
                    "emoji": "🩸",
                    "type": "stats",
                    "summary": f"{store['total_files']} files stored, {store['unique_blobs']} unique blobs",
                    "detail": store,
                })
    except Exception:
        pass
    return events

async def _probe_gland(client: httpx.AsyncClient) -> list[dict]:
    """Get recent LLM usage from Gland."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/gland/usage/recent?limit=5", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for rec in data.get("records", [])[:3]:
                events.append({
                    "organ": "gland",
                    "emoji": "🧪",
                    "type": "llm_call",
                    "summary": f"{rec.get('model', 'unknown')} — {rec.get('total_tokens', 0)} tokens",
                    "detail": rec,
                    "timestamp": rec.get("timestamp"),
                })
    except Exception:
        pass
    return events

async def _probe_immune(client: httpx.AsyncClient) -> list[dict]:
    """Get recent security events from Immune."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/immune/audit/log?limit=5", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for entry in data.get("entries", [])[:3]:
                events.append({
                    "organ": "immune",
                    "emoji": "🛡",
                    "type": "security",
                    "summary": f"{entry.get('action', 'unknown')} — {entry.get('detail', '')}",
                    "detail": entry,
                    "timestamp": entry.get("timestamp"),
                })
    except Exception:
        pass
    return events

async def _probe_trajectory(client: httpx.AsyncClient) -> list[dict]:
    """Get recent trajectory events."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/trajectory/events?limit=5", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for ev in data.get("events", [])[:3]:
                events.append({
                    "organ": "trajectory",
                    "emoji": "📊",
                    "type": "agent_event",
                    "summary": f"[{ev.get('agent_id', '')}] {ev.get('event_type', '')}: {ev.get('content', '')[:80]}",
                    "detail": ev,
                    "timestamp": ev.get("timestamp"),
                })
    except Exception:
        pass
    return events

async def _probe_echo(client: httpx.AsyncClient) -> list[dict]:
    """Get recent message dispatches from Echo."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/echo/history?limit=5", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for msg in data.get("messages", [])[:3]:
                events.append({
                    "organ": "echo",
                    "emoji": "🔊",
                    "type": "message",
                    "summary": f"[{msg.get('channel', '')}] {msg.get('title', '')}",
                    "detail": msg,
                    "timestamp": msg.get("timestamp"),
                })
    except Exception:
        pass
    return events

async def _probe_mirror(client: httpx.AsyncClient) -> list[dict]:
    """Get recent sandbox activities from Mirror."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/mirror/sandboxes", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for sb in data.get("sandboxes", [])[:3]:
                events.append({
                    "organ": "mirror",
                    "emoji": "🪞",
                    "type": "sandbox",
                    "summary": f"Sandbox '{sb.get('name', sb.get('sandbox_id', ''))}' — {sb.get('status', '')}",
                    "detail": sb,
                    "timestamp": sb.get("created_at"),
                })
    except Exception:
        pass
    return events

async def _probe_link(client: httpx.AsyncClient) -> list[dict]:
    """Get recent webhook events from Link."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/link/events?limit=5", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for ev in data.get("events", [])[:3]:
                events.append({
                    "organ": "link",
                    "emoji": "🔗",
                    "type": "webhook",
                    "summary": f"[{ev.get('direction', '')}] {ev.get('event_type', '')}: {ev.get('payload_summary', '')[:60]}",
                    "detail": ev,
                    "timestamp": ev.get("timestamp"),
                })
    except Exception:
        pass
    return events

async def _probe_limb(client: httpx.AsyncClient) -> list[dict]:
    """Get recent RPA tasks from Limb."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/limb/tasks?limit=5", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for task in data.get("tasks", [])[:3]:
                events.append({
                    "organ": "limb",
                    "emoji": "💪",
                    "type": "rpa_task",
                    "summary": f"Task '{task.get('name', '')}' — {task.get('status', '')}",
                    "detail": task,
                    "timestamp": task.get("created_at"),
                })
    except Exception:
        pass
    return events

async def _probe_cron(client: httpx.AsyncClient) -> list[dict]:
    """Get recent cron job executions."""
    events = []
    try:
        r = await client.get(f"{_BASE}/api/cron/jobs", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            for job in (data.get("jobs", data) if isinstance(data, (list, dict)) else [])[:3]:
                if isinstance(job, dict):
                    events.append({
                        "organ": "will",
                        "emoji": "✨",
                        "type": "cron_job",
                        "summary": f"Cron '{job.get('name', job.get('id', ''))}' — last: {job.get('last_run', 'never')}",
                        "detail": job,
                    })
    except Exception:
        pass
    return events

_PROBES = [
    _probe_vein,
    _probe_gland,
    _probe_immune,
    _probe_trajectory,
    _probe_echo,
    _probe_mirror,
    _probe_link,
    _probe_limb,
    _probe_cron,
]


async def _collect_all_events() -> list[dict]:
    """Run all probes in parallel and collect events."""
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[p(client) for p in _PROBES], return_exceptions=True)

    events = []
    now = time.time()
    for result in results:
        if isinstance(result, list):
            for ev in result:
                ev.setdefault("id", uuid.uuid4().hex[:12])
                ev.setdefault("collected_at", now)
                events.append(ev)

    # Sort by timestamp (most recent first)
    events.sort(key=lambda e: e.get("timestamp", e.get("collected_at", 0)), reverse=True)
    return events


# ── Endpoints ──────────────────────────────────────────────────

@router.get("/stream")
async def get_event_stream(
    limit: int = Query(default=50, ge=1, le=200),
    organ: str = Query(default=None, description="Filter by organ name"),
):
    """Get the latest aggregated event stream from all organs."""
    events = await _collect_all_events()

    # Also include cached events from buffer
    all_events = list(_event_buffer) + events
    # Deduplicate by id
    seen = set()
    unique = []
    for ev in all_events:
        eid = ev.get("id", "")
        if eid not in seen:
            seen.add(eid)
            unique.append(ev)

    # Filter by organ if specified
    if organ:
        unique = [e for e in unique if e.get("organ") == organ]

    # Update buffer
    for ev in events:
        _event_buffer.appendleft(ev)

    return {
        "events": unique[:limit],
        "total": len(unique),
        "organs_probed": len(_PROBES),
        "collected_at": time.time(),
    }


@router.get("/stream/summary")
async def get_stream_summary():
    """Get a summary of recent system activity."""
    events = await _collect_all_events()

    # Aggregate by organ
    by_organ: dict[str, int] = {}
    for ev in events:
        organ = ev.get("organ", "unknown")
        by_organ[organ] = by_organ.get(organ, 0) + 1

    # Aggregate by type
    by_type: dict[str, int] = {}
    for ev in events:
        etype = ev.get("type", "unknown")
        by_type[etype] = by_type.get(etype, 0) + 1

    return {
        "total_events": len(events),
        "by_organ": by_organ,
        "by_type": by_type,
        "most_active_organ": max(by_organ, key=by_organ.get) if by_organ else None,
        "collected_at": time.time(),
    }


@router.post("/stream/refresh")
async def refresh_stream():
    """Force refresh the event stream cache."""
    events = await _collect_all_events()
    _event_buffer.clear()
    for ev in events:
        _event_buffer.appendleft(ev)
    return {
        "refreshed": True,
        "events_collected": len(events),
    }


@router.get("/health")
async def stream_health():
    """Event stream health check."""
    return {
        "status": "ok",
        "component": "EventStream",
        "buffer_size": len(_event_buffer),
        "probes_configured": len(_PROBES),
    }
