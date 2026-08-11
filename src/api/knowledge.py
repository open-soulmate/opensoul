from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from src.models.knowledge import KnowledgeCreate, KnowledgeUpdate, KnowledgeResponse
from src.services import knowledge as knowledge_service

router = APIRouter()


@router.get("/", response_model=list[KnowledgeResponse])
async def list_knowledge(
    user_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    content_type: str | None = None,
    domain: str | None = None,
    tag: str | None = None,
):
    """List knowledge items with pagination and filters."""
    return await knowledge_service.list_knowledge(
        user_id, offset=offset, limit=limit,
        content_type=content_type, domain=domain, tag=tag,
    )


@router.get("/{knowledge_id}", response_model=KnowledgeResponse)
async def get_knowledge(knowledge_id: UUID, user_id: UUID):
    """Get a single knowledge item by ID."""
    row = await knowledge_service.get_knowledge(knowledge_id, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return row


@router.post("/", response_model=KnowledgeResponse)
async def create_knowledge(data: KnowledgeCreate, user_id: UUID):
    """Create a new knowledge item."""
    row = await knowledge_service.create_knowledge(data, user_id)
    return row


@router.put("/{knowledge_id}", response_model=KnowledgeResponse)
async def update_knowledge(knowledge_id: UUID, data: KnowledgeUpdate, user_id: UUID):
    """Update an existing knowledge item."""
    row = await knowledge_service.update_knowledge(knowledge_id, data, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return row


@router.delete("/{knowledge_id}")
async def delete_knowledge(knowledge_id: UUID, user_id: UUID):
    """Delete a knowledge item."""
    deleted = await knowledge_service.delete_knowledge(knowledge_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return {"deleted": True}


@router.post("/{knowledge_id}/star")
async def star_knowledge(knowledge_id: UUID, user_id: UUID):
    """Toggle star (favorite) on a knowledge item."""
    result = await knowledge_service.toggle_star(knowledge_id, user_id)
    if not result:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return {"id": knowledge_id, "starred": result["starred"]}


@router.post("/{knowledge_id}/pin")
async def pin_knowledge(knowledge_id: UUID, user_id: UUID):
    """Toggle pin on a knowledge item."""
    result = await knowledge_service.toggle_pin(knowledge_id, user_id)
    if not result:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return {"id": knowledge_id, "pinned": result["pinned"]}
