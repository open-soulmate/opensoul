from uuid import UUID

from fastapi import APIRouter, Query

from src.models.entity import RelationCreate, RelationResponse, GraphData
from src.services.graph import create_relation, get_graph, list_relations
from src.services.entity import list_entities, get_entity_with_relations

router = APIRouter()


def _resolve_user_id(user_id: str) -> UUID:
    """Convert user_id to UUID — accept both UUID strings and plain usernames."""
    try:
        return UUID(user_id)
    except ValueError:
        import hashlib
        h = hashlib.md5(user_id.encode()).hexdigest()
        return UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


@router.get("/entities", response_model=list)
async def get_entities(
    user_id: str = Query("default", description="User ID (UUID or username)"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Entity list, optionally filtered by type."""
    uid = _resolve_user_id(user_id)
    return await list_entities(uid, entity_type, offset, limit)


@router.get("/entities/{entity_id}")
async def get_entity_detail(entity_id: UUID, user_id: str = Query("default")):
    """Entity detail with its relations."""
    uid = _resolve_user_id(user_id)
    entity = await get_entity_with_relations(entity_id, uid)
    if not entity:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


@router.post("/relations", response_model=RelationResponse)
async def add_relation(data: RelationCreate):
    return await create_relation(data)


@router.get("/relations")
async def get_relations(
    user_id: str = Query("default", description="User ID (UUID or username)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Relation list for a user."""
    uid = _resolve_user_id(user_id)
    return await list_relations(uid, offset, limit)


@router.get("/full", response_model=GraphData)
async def get_full_graph(user_id: str = Query("default", description="User ID (UUID or username)"), depth: int = Query(2, ge=1, le=5)):
    """Full graph data (nodes + edges) for frontend G6 rendering."""
    uid = _resolve_user_id(user_id)
    return await get_graph(uid, depth)


@router.get("/", response_model=GraphData)
async def get_graph_data(user_id: str = Query("default", description="User ID (UUID or username)"), depth: int = 2, entity_id: UUID | None = None):
    uid = _resolve_user_id(user_id)
    return await get_graph(uid, depth, entity_id)
