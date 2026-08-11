from uuid import UUID

from fastapi import APIRouter, HTTPException

from src.models.knowledge import KnowledgeCreate, KnowledgeUpdate, KnowledgeResponse
from src.services import knowledge as knowledge_service

router = APIRouter()


@router.post("/", response_model=KnowledgeResponse)
async def create(data: KnowledgeCreate, user_id: UUID):
    row = await knowledge_service.create_knowledge(data, user_id)
    return row


@router.get("/", response_model=list[KnowledgeResponse])
async def list_all(user_id: UUID, offset: int = 0, limit: int = 20):
    return await knowledge_service.list_knowledge(user_id, offset, limit)


@router.get("/{knowledge_id}", response_model=KnowledgeResponse)
async def get_one(knowledge_id: UUID, user_id: UUID):
    row = await knowledge_service.get_knowledge(knowledge_id, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return row


@router.patch("/{knowledge_id}", response_model=KnowledgeResponse)
async def update(knowledge_id: UUID, data: KnowledgeUpdate, user_id: UUID):
    row = await knowledge_service.update_knowledge(knowledge_id, data, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return row


@router.delete("/{knowledge_id}")
async def delete(knowledge_id: UUID, user_id: UUID):
    deleted = await knowledge_service.delete_knowledge(knowledge_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return {"deleted": True}
