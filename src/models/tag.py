from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class TagBase(BaseModel):
    name: str
    color: str = "#6366f1"


class TagCreate(TagBase):
    pass


class TagUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


class TagResponse(TagBase):
    id: UUID
    user_id: UUID
    usage_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}
