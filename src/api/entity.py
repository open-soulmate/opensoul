from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from src.models.entity import EntityCreate, EntityUpdate, EntityResponse
from src.services import entity as entity_service

router = APIRouter()


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
