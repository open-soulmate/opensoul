from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from src.services.rag import rag_query
from src.services import knowledge as knowledge_service
from src.models.knowledge import KnowledgeCreate

router = APIRouter()


class AgentRememberRequest(BaseModel):
    title: str
    content: str
    tags: list[str] = []


class AgentRecallRequest(BaseModel):
    question: str
    top_k: int = 5


@router.post("/remember")
async def remember(req: AgentRememberRequest, user_id: UUID):
    """Agent stores a new memory."""
    data = KnowledgeCreate(title=req.title, content=req.content, tags=req.tags)
    row = await knowledge_service.create_knowledge(data, user_id)
    return {"status": "remembered", "id": row["id"]}


@router.post("/recall")
async def recall(req: AgentRecallRequest, user_id: UUID):
    """Agent retrieves relevant memories."""
    result = await rag_query(req.question, user_id, req.top_k)
    return result
