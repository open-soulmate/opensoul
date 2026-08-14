from uuid import UUID

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
