from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from src.services.search import semantic_search, fulltext_search, hybrid_search

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    mode: str = "hybrid"  # semantic, fulltext, hybrid
    limit: int = 10


@router.post("/")
async def search(req: SearchRequest, user_id: UUID):
    if req.mode == "semantic":
        results = await semantic_search(req.query, user_id, req.limit)
    elif req.mode == "fulltext":
        results = await fulltext_search(req.query, user_id, req.limit)
    else:
        results = await hybrid_search(req.query, user_id, req.limit)
    return {"query": req.query, "mode": req.mode, "results": results}
