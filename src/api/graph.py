from uuid import UUID

from fastapi import APIRouter, Query

from src.models.entity import RelationCreate, RelationResponse, GraphData
from src.services.graph import create_relation, get_graph, list_relations
from src.services.entity import list_entities, get_entity_with_relations

router = APIRouter()


@router.get("/entities", response_model=list)
async def get_entities(
    user_id: UUID,
    entity_type: str | None = Query(None, description="Filter by entity type"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Entity list, optionally filtered by type."""
    return await list_entities(user_id, entity_type, offset, limit)


@router.get("/entities/{entity_id}")
async def get_entity_detail(entity_id: UUID, user_id: UUID):
    """Entity detail with its relations."""
    entity = await get_entity_with_relations(entity_id, user_id)
    if not entity:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


@router.post("/relations", response_model=RelationResponse)
async def add_relation(data: RelationCreate):
    return await create_relation(data)


@router.get("/relations")
async def get_relations(
    user_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Relation list for a user."""
    return await list_relations(user_id, offset, limit)


@router.get("/full", response_model=GraphData)
async def get_full_graph(user_id: UUID, depth: int = Query(2, ge=1, le=5)):
    """Full graph data (nodes + edges) for frontend G6 rendering."""
    return await get_graph(user_id, depth)


@router.get("/", response_model=GraphData)
async def get_graph_data(user_id: UUID, depth: int = 2, entity_id: UUID | None = None):
    return await get_graph(user_id, depth, entity_id)
