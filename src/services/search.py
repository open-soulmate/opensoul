from uuid import UUID

from src.database.meilisearch import meili_client
from src.database.qdrant import qdrant_client
from src.services.embedding import get_embedding


async def semantic_search(query: str, user_id: UUID, limit: int = 10) -> list[dict]:
    """Search using vector similarity."""
    query_vector = await get_embedding(query)
    results = qdrant_client.search(query_vector, limit=limit, user_id=user_id)
    return [
        {
            "id": hit.payload.get("knowledge_id"),
            "chunk": hit.payload.get("content"),
            "score": hit.score,
        }
        for hit in results
    ]


async def fulltext_search(query: str, user_id: UUID, limit: int = 10) -> list[dict]:
    """Search using Meilisearch full-text search."""
    result = meili_client.search(query, limit=limit, filters=f'user_id = "{user_id}"')
    return result.get("hits", [])


async def hybrid_search(query: str, user_id: UUID, limit: int = 10) -> list[dict]:
    """Combine semantic and fulltext search results."""
    semantic_results = await semantic_search(query, user_id, limit)
    fulltext_results = await fulltext_search(query, user_id, limit)

    # Merge results, dedup by knowledge id, prefer semantic scores
    seen = {}
    merged = []

    for r in fulltext_results:
        kid = r.get("id")
        if kid and kid not in seen:
            seen[kid] = True
            merged.append({"id": kid, "title": r.get("title"), "content": r.get("content", "")[:200], "source": "fulltext", "score": r.get("_rankingScore", 0)})

    for r in semantic_results:
        kid = r.get("id")
        if kid and kid not in seen:
            seen[kid] = True
            merged.append({"id": kid, "chunk": r.get("chunk", ""), "source": "semantic", "score": r.get("score", 0)})
        elif kid and kid in seen:
            # Boost existing entry
            for m in merged:
                if m["id"] == kid:
                    m["score"] = max(m.get("score", 0), r.get("score", 0))
                    m["sources"] = "semantic+fulltext"

    merged.sort(key=lambda x: x.get("score", 0), reverse=True)
    return merged[:limit]
