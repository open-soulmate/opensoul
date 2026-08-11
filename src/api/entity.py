from uuid import UUID

from fastapi import APIRouter, HTTPException

from src.models.entity import EntityCreate, EntityUpdate, EntityResponse
from src.services import entity as entity_service

router = APIRouter()


@router.post("/", response_model=EntityResponse)
async def create(data: EntityCreate, user_id: UUID):
    return await entity_service.create_entity(data, user_id)


@router.get("/", response_model=list[EntityResponse])
async def list_all(user_id: UUID, entity_type: str | None = None, offset: int = 0, limit: int = 50):
    return await entity_service.list_entities(user_id, entity_type, offset, limit)


@router.get("/{entity_id}", response_model=EntityResponse)
async def get_one(entity_id: UUID, user_id: UUID):
    row = await entity_service.get_entity(entity_id, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Entity not found")
    return row


@router.patch("/{entity_id}", response_model=EntityResponse)
async def update(entity_id: UUID, data: EntityUpdate, user_id: UUID):
    row = await entity_service.update_entity(entity_id, data, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Entity not found")
    return row


@router.delete("/{entity_id}")
async def delete(entity_id: UUID, user_id: UUID):
    deleted = await entity_service.delete_entity(entity_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Entity not found")
    return {"deleted": True}
