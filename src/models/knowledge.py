from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class KnowledgeBase(BaseModel):
    title: str
    content: str
    source: str = ""
    content_type: str = "text"
    metadata: dict = Field(default_factory=dict)


class KnowledgeCreate(KnowledgeBase):
    tags: list[str] = Field(default_factory=list)


class KnowledgeUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    source: str | None = None
    content_type: str | None = None
    metadata: dict | None = None
    tags: list[str] | None = None


class KnowledgeResponse(KnowledgeBase):
    id: UUID
    user_id: UUID
    tags: list[str] = Field(default_factory=list)
    embedding_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeChunk(BaseModel):
    id: UUID
    knowledge_id: UUID
    chunk_index: int
    content: str
    embedding_id: str | None = None
    token_count: int = 0
