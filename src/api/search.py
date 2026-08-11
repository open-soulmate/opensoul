from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.services.search import semantic_search, fulltext_search, hybrid_search

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    mode: str = "hybrid"  # semantic, fulltext, hybrid
    limit: int = 10


@router.get("/")
async def search_get(
    q: str = Query(..., description="Search query"),
    user_id: UUID = Query(...),
    mode: str = Query("hybrid", description="Search mode: semantic, fulltext, hybrid"),
    limit: int = Query(10, ge=1, le=50),
):
    """Full-text + vector hybrid search via GET."""
    if mode == "semantic":
        results = await semantic_search(q, user_id, limit)
    elif mode == "fulltext":
        results = await fulltext_search(q, user_id, limit)
    else:
        results = await hybrid_search(q, user_id, limit)
    return {"query": q, "mode": mode, "results": results}


@router.post("/")
async def search_post(req: SearchRequest, user_id: UUID):
    """Search via POST body."""
    if req.mode == "semantic":
        results = await semantic_search(req.query, user_id, req.limit)
    elif req.mode == "fulltext":
        results = await fulltext_search(req.query, user_id, req.limit)
    else:
        results = await hybrid_search(req.query, user_id, req.limit)
    return {"query": req.query, "mode": req.mode, "results": results}
