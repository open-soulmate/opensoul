"""OpenHippo API — 海马体：记忆生命周期管理、会话管理、衰减遗忘。"""

import time
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.hippo.memory_store import MemoryStore
from src.hippo.session import SessionManager, SessionStatus
from src.hippo.decay import DecayStrategy

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
store = MemoryStore(strategy=DecayStrategy.ACCESS_REINFORCED, half_life_hours=24.0)
sessions = SessionManager()


# ── Request Schemas ────────────────────────────────────────

class MemoryCreateRequest(BaseModel):
    session_id: str
    content: str
    importance: float = 0.5
    tags: list[str] = []
    metadata: dict = {}


class MemoryUpdateRequest(BaseModel):
    content: str | None = None
    importance: float | None = None
    tags: list[str] | None = None


class SessionCreateRequest(BaseModel):
    user_id: str = ""
    title: str = ""
    metadata: dict = {}


class DecayConfigRequest(BaseModel):
    strategy: str | None = None
    half_life_hours: float | None = None
    archive_threshold: float | None = None
    forget_threshold: float | None = None


# ── Health ─────────────────────────────────────────────────

@router.get("/health")
async def health():
    """OpenHippo health check."""
    return {
        "status": "ok",
        "component": "OpenHippo",
        "memory": store.get_stats(),
        "sessions": sessions.get_stats(),
    }


# ── Memory CRUD ────────────────────────────────────────────

@router.post("/memories")
async def create_memory(req: MemoryCreateRequest):
    """Create a new short-term memory."""
    # Auto-create session if needed
    session = sessions.get(req.session_id)
    if not session:
        session = sessions.create(user_id="", title=f"Auto: {req.session_id}")
        # Use the provided session_id
        session.session_id = req.session_id

    mem = store.add(
        session_id=req.session_id,
        content=req.content,
        importance=req.importance,
        tags=req.tags,
        metadata=req.metadata,
    )
    sessions.increment_memory_count(req.session_id)
    return {
        "memory_id": mem.memory_id,
        "session_id": mem.session_id,
        "retention": mem.retention,
        "created_at": mem.created_at,
    }


@router.get("/memories")
async def list_memories(
    session_id: str = Query(default=None),
    tag: str = Query(default=None),
    min_retention: float = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List/search memories."""
    tags = [tag] if tag else None
    memories = store.search(
        session_id=session_id,
        tags=tags,
        min_retention=min_retention,
        include_archived=include_archived,
        limit=limit,
    )
    return {
        "memories": [
            {
                "memory_id": m.memory_id,
                "session_id": m.session_id,
                "content": m.content,
                "importance": m.importance,
                "tags": m.tags,
                "retention": m.retention,
                "access_count": m.access_count,
                "archived": m.archived,
                "created_at": m.created_at,
                "last_accessed_at": m.last_accessed_at,
            }
            for m in memories
        ],
        "total": len(memories),
    }


@router.get("/memories/{memory_id}")
async def get_memory(memory_id: str):
    """Get a single memory (refreshes access)."""
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(404, "Memory not found")
    decay = store.decay_engine.calculate(
        mem.created_at, mem.last_accessed_at, mem.access_count, mem.importance
    )
    return {
        "memory_id": mem.memory_id,
        "session_id": mem.session_id,
        "content": mem.content,
        "importance": mem.importance,
        "tags": mem.tags,
        "metadata": mem.metadata,
        "retention": decay.retention,
        "access_count": mem.access_count,
        "archived": mem.archived,
        "created_at": mem.created_at,
        "last_accessed_at": mem.last_accessed_at,
    }


@router.patch("/memories/{memory_id}")
async def update_memory(memory_id: str, req: MemoryUpdateRequest):
    """Update a memory's content, importance, or tags."""
    kwargs = {}
    if req.content is not None:
        kwargs["content"] = req.content
    if req.importance is not None:
        kwargs["importance"] = req.importance
    if req.tags is not None:
        kwargs["tags"] = req.tags
    mem = store.update(memory_id, **kwargs)
    if not mem:
        raise HTTPException(404, "Memory not found")
    return {"memory_id": mem.memory_id, "updated": True}


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a memory."""
    if not store.delete(memory_id):
        raise HTTPException(404, "Memory not found")
    return {"deleted": True}


# ── Decay Control ──────────────────────────────────────────

@router.post("/decay/run")
async def run_decay_cycle():
    """Run a decay cycle: update retention, archive, forget."""
    result = store.run_decay_cycle()
    return result


@router.get("/decay/config")
async def get_decay_config():
    """Get current decay configuration."""
    return {
        "strategy": store.decay_engine.strategy.value,
        "half_life_hours": store.decay_engine.half_life_hours,
        "archive_threshold": store.decay_engine.archive_threshold,
        "forget_threshold": store.decay_engine.forget_threshold,
    }


@router.put("/decay/config")
async def update_decay_config(req: DecayConfigRequest):
    """Update decay configuration."""
    if req.strategy:
        try:
            store.decay_engine.strategy = DecayStrategy(req.strategy)
        except ValueError:
            raise HTTPException(400, f"Invalid strategy. Valid: {[s.value for s in DecayStrategy]}")
    if req.half_life_hours is not None:
        store.decay_engine.half_life_hours = req.half_life_hours
    if req.archive_threshold is not None:
        store.decay_engine.archive_threshold = req.archive_threshold
    if req.forget_threshold is not None:
        store.decay_engine.forget_threshold = req.forget_threshold
    return await get_decay_config()


@router.post("/decay/simulate")
async def simulate_decay(
    age_hours: float = Query(default=24.0),
    importance: float = Query(default=0.5),
    access_count: int = Query(default=0),
):
    """Simulate decay for given parameters without affecting real memories."""
    now = time.time()
    created_at = now - age_hours * 3600
    last_accessed = now - age_hours * 0.5 * 3600  # accessed halfway through
    result = store.decay_engine.calculate(created_at, last_accessed, access_count, importance)
    return {
        "retention": result.retention,
        "should_archive": result.should_archive,
        "should_forget": result.should_forget,
        "strategy": result.strategy,
        "simulated_age_hours": age_hours,
        "importance": importance,
        "access_count": access_count,
    }


# ── Session Management ─────────────────────────────────────

@router.post("/sessions")
async def create_session(req: SessionCreateRequest):
    """Create a new session."""
    session = sessions.create(user_id=req.user_id, title=req.title, metadata=req.metadata)
    return {
        "session_id": session.session_id,
        "title": session.title,
        "status": session.status.value,
        "created_at": session.created_at,
    }


@router.get("/sessions")
async def list_sessions(
    user_id: str = Query(default=None),
    status: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List sessions."""
    session_status = None
    if status:
        try:
            session_status = SessionStatus(status)
        except ValueError:
            raise HTTPException(400, f"Invalid status. Valid: {[s.value for s in SessionStatus]}")
    session_list = sessions.list_sessions(user_id=user_id, status=session_status, limit=limit)
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "user_id": s.user_id,
                "title": s.title,
                "status": s.status.value,
                "memory_count": s.memory_count,
                "created_at": s.created_at,
                "last_active_at": s.last_active_at,
            }
            for s in session_list
        ],
        "total": len(session_list),
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session details."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return {
        "session_id": session.session_id,
        "user_id": session.user_id,
        "title": session.title,
        "status": session.status.value,
        "memory_count": session.memory_count,
        "total_tokens": session.total_tokens,
        "created_at": session.created_at,
        "last_active_at": session.last_active_at,
        "metadata": session.metadata,
    }


@router.post("/sessions/{session_id}/archive")
async def archive_session(session_id: str):
    """Archive a session."""
    if not sessions.archive(session_id):
        raise HTTPException(404, "Session not found")
    return {"archived": True}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session."""
    if not sessions.delete(session_id):
        raise HTTPException(404, "Session not found")
    return {"deleted": True}


@router.post("/sessions/lifecycle-check")
async def run_lifecycle_check():
    """Run session lifecycle check (idle detection, expiry)."""
    result = sessions.run_lifecycle_check()
    return result


# ── Stats ──────────────────────────────────────────────────

@router.get("/stats")
async def hippo_stats():
    """OpenHippo detailed statistics."""
    mem_stats = store.get_stats()
    sess_stats = sessions.get_stats()
    all_memories = store.search(limit=1000)
    by_tag = {}
    for m in all_memories:
        for t in (m.tags or []):
            by_tag[t] = by_tag.get(t, 0) + 1

    return {
        "status": "ok",
        "component": "OpenHippo",
        "memory": mem_stats,
        "sessions": sess_stats,
        "by_tag": by_tag,
        "decay_config": {
            "strategy": store.decay_engine.strategy.value,
            "half_life_hours": store.decay_engine.half_life_hours,
            "archive_threshold": store.decay_engine.archive_threshold,
            "forget_threshold": store.decay_engine.forget_threshold,
        },
    }
