from uuid import UUID

from fastapi import APIRouter

from src.models.entity import RelationCreate, RelationResponse, GraphData
from src.services.graph import create_relation, get_graph

router = APIRouter()


@router.post("/relations", response_model=RelationResponse)
async def add_relation(data: RelationCreate):
    return await create_relation(data)


@router.get("/", response_model=GraphData)
async def get_graph_data(user_id: UUID, depth: int = 2, entity_id: UUID | None = None):
    return await get_graph(user_id, depth, entity_id)
