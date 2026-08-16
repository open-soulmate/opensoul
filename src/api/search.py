from uuid import UUID
import asyncio

from fastapi import APIRouter, Query
from pydantic import BaseModel
from src.services.search import semantic_search, fulltext_search, hybrid_search

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    mode: str = "hybrid"  # semantic, fulltext, hybrid
    limit: int = 10


def _resolve_user_id(user_id: str) -> UUID:
    """Convert user_id to UUID — accept both UUID strings and plain usernames."""
    try:
        return UUID(user_id)
    except ValueError:
        # Hash non-UUID strings (e.g. usernames) into a deterministic UUID
        import hashlib
        h = hashlib.md5(user_id.encode()).hexdigest()
        return UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


@router.get("/")
async def search_get(
    q: str = Query(..., description="Search query"),
    user_id: str = Query("default", description="User ID (UUID or username)"),
    mode: str = Query("hybrid", description="Search mode: semantic, fulltext, hybrid"),
    limit: int = Query(10, ge=1, le=50),
):
    """Full-text + vector hybrid search via GET."""
    uid = _resolve_user_id(user_id)
    if mode == "semantic":
        results = await semantic_search(q, uid, limit)
    elif mode == "fulltext":
        results = await fulltext_search(q, uid, limit)
    else:
        results = await hybrid_search(q, uid, limit)
    return {"query": q, "mode": mode, "results": results}


@router.post("/")
async def search_post(req: SearchRequest, user_id: str = "default"):
    """Search via POST body."""
    uid = _resolve_user_id(user_id)
    if req.mode == "semantic":
        results = await semantic_search(req.query, uid, req.limit)
    elif req.mode == "fulltext":
        results = await fulltext_search(req.query, uid, req.limit)
    else:
        results = await hybrid_search(req.query, uid, req.limit)
    return {"query": req.query, "mode": req.mode, "results": results}


# ── Unified Search — searches across multiple subsystems ──────

async def _search_knowledge(query: str, limit: int) -> list[dict]:
    """Search knowledge base."""
    try:
        results = await hybrid_search(query, _resolve_user_id("default"), limit)
        return [
            {**r, "source": "knowledge", "icon": "📚"}
            for r in results
        ]
    except Exception:
        return []


async def _search_files(query: str, limit: int) -> list[dict]:
    """Search files in Vein."""
    try:
        from src.vein.file_store import FileStore
        store = FileStore()
        files = store.list_files(name_filter=query, limit=limit)
        return [
            {
                "source": "files",
                "icon": "📄",
                "title": f.name,
                "snippet": f"{f.mime_type} · {f.size} bytes",
                "file_id": f.file_id,
                "size": f.size,
                "mime_type": f.mime_type,
                "created_at": f.created_at,
            }
            for f in files
        ]
    except Exception:
        return []


async def _search_events(query: str, limit: int) -> list[dict]:
    """Search event stream."""
    try:
        from src.api.event_stream import _event_buffer
        query_lower = query.lower()
        matches = []
        for ev in reversed(_event_buffer):
            summary = str(ev.get("summary", "")).lower()
            organ = str(ev.get("organ", "")).lower()
            etype = str(ev.get("type", "")).lower()
            if query_lower in summary or query_lower in organ or query_lower in etype:
                matches.append({
                    "source": "events",
                    "icon": ev.get("emoji", "⚡"),
                    "title": ev.get("summary", ""),
                    "snippet": f"[{ev.get('organ', '')}] {ev.get('type', '')}",
                    "timestamp": ev.get("collected_at") or ev.get("timestamp"),
                    "organ": ev.get("organ", ""),
                })
                if len(matches) >= limit:
                    break
        return matches
    except Exception:
        return []


async def _search_agents(query: str, limit: int) -> list[dict]:
    """Search agents list."""
    try:
        import json
        import os
        agents_path = os.path.expanduser("~/.openmate/agents.json")
        if not os.path.exists(agents_path):
            return []
        with open(agents_path) as f:
            agents = json.load(f)
        query_lower = query.lower()
        matches = []
        for a in agents:
            name = str(a.get("name", "")).lower()
            desc = str(a.get("description", "")).lower()
            if query_lower in name or query_lower in desc:
                matches.append({
                    "source": "agents",
                    "icon": "🤖",
                    "title": a.get("name", "Unknown"),
                    "snippet": a.get("description", ""),
                    "agent_id": a.get("id", ""),
                })
                if len(matches) >= limit:
                    break
        return matches
    except Exception:
        return []


async def _search_courses(query: str, limit: int) -> list[dict]:
    """Search learning courses."""
    try:
        from src.learn.course_engine import CourseEngine
        engine = CourseEngine()
        query_lower = query.lower()
        matches = []
        for course in engine.list_courses():
            title = course.title.lower()
            desc = course.description.lower()
            tags = " ".join(course.tags).lower()
            if query_lower in title or query_lower in desc or query_lower in tags:
                matches.append({
                    "source": "courses",
                    "icon": "📖",
                    "title": course.title,
                    "snippet": course.description[:100],
                    "course_id": course.course_id,
                    "status": course.status,
                })
                if len(matches) >= limit:
                    break
        return matches
    except Exception:
        return []


@router.get("/unified")
async def unified_search(
    q: str = Query(..., description="Search query"),
    sources: str = Query("all", description="Comma-separated sources: knowledge,files,events,agents,courses,all"),
    limit: int = Query(10, ge=1, le=50),
):
    """Unified search across all subsystems: knowledge, files, events, agents, courses.

    Returns results grouped by source with relevance ranking.
    """
    source_list = [s.strip() for s in sources.split(",")]
    search_all = "all" in source_list

    tasks = {}
    if search_all or "knowledge" in source_list:
        tasks["knowledge"] = _search_knowledge(q, limit)
    if search_all or "files" in source_list:
        tasks["files"] = _search_files(q, limit)
    if search_all or "events" in source_list:
        tasks["events"] = _search_events(q, limit)
    if search_all or "agents" in source_list:
        tasks["agents"] = _search_agents(q, limit)
    if search_all or "courses" in source_list:
        tasks["courses"] = _search_courses(q, limit)

    # Run all searches in parallel
    results_list = await asyncio.gather(*tasks.values(), return_exceptions=True)

    by_source = {}
    total = 0
    for source_name, results in zip(tasks.keys(), results_list):
        if isinstance(results, list):
            by_source[source_name] = results
            total += len(results)
        else:
            by_source[source_name] = []

    return {
        "query": q,
        "total": total,
        "by_source": by_source,
        "sources_searched": list(tasks.keys()),
    }
