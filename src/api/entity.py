from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from src.models.entity import EntityCreate, EntityUpdate, EntityResponse
from src.services import entity as entity_service
from src.database.postgres import db_pool

router = APIRouter()


@router.get("/health")
async def entity_health():
    """Entity system health check."""
    return {"status": "ok", "component": "EntitySystem"}


@router.get("/stats")
async def entity_stats():
    """Get entity system statistics."""
    try:
        total = await db_pool.fetchval("SELECT COUNT(*) FROM entities") or 0
        by_type = await db_pool.fetch(
            "SELECT entity_type, COUNT(*) as cnt FROM entities GROUP BY entity_type ORDER BY cnt DESC LIMIT 10"
        )
        return {
            "status": "ok",
            "component": "EntitySystem",
            "total_entities": total,
            "by_type": {r["entity_type"]: r["cnt"] for r in (by_type or [])},
        }
    except Exception:
        return {"status": "ok", "component": "EntitySystem", "total_entities": 0, "by_type": {}}


def _resolve_user_id(user_id: str) -> UUID:
    """Convert user_id to UUID — accept both UUID strings and plain usernames."""
    try:
        return UUID(user_id)
    except ValueError:
        import hashlib
        h = hashlib.md5(user_id.encode()).hexdigest()
        return UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


@router.post("/", response_model=EntityResponse)
async def create(data: EntityCreate, user_id: str = Query("default")):
    uid = _resolve_user_id(user_id)
    return await entity_service.create_entity(data, uid)


@router.get("/", response_model=list[EntityResponse])
async def list_all(user_id: str = Query("default"), entity_type: str | None = None, offset: int = 0, limit: int = 50):
    uid = _resolve_user_id(user_id)
    return await entity_service.list_entities(uid, entity_type, offset, limit)


@router.get("/{entity_id}", response_model=EntityResponse)
async def get_one(entity_id: UUID, user_id: str = Query("default")):
    uid = _resolve_user_id(user_id)
    row = await entity_service.get_entity(entity_id, uid)
    if not row:
        raise HTTPException(status_code=404, detail="Entity not found")
    return row


@router.patch("/{entity_id}", response_model=EntityResponse)
async def update(entity_id: UUID, data: EntityUpdate, user_id: str = Query("default")):
    uid = _resolve_user_id(user_id)
    row = await entity_service.update_entity(entity_id, data, uid)
    if not row:
        raise HTTPException(status_code=404, detail="Entity not found")
    return row


@router.delete("/{entity_id}")
async def delete(entity_id: UUID, user_id: str = Query("default")):
    uid = _resolve_user_id(user_id)
    deleted = await entity_service.delete_entity(entity_id, uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Entity not found")
    return {"deleted": True}
