from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from src.services.rag import rag_query

router = APIRouter()


class ChatRequest(BaseModel):
    question: str
    top_k: int = 5


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict]


@router.post("/", response_model=ChatResponse)
async def chat(req: ChatRequest, user_id: UUID):
    result = await rag_query(req.question, user_id, req.top_k)
    return result
