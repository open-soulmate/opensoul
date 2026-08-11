from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class EntityBase(BaseModel):
    name: str
    entity_type: str  # person, place, concept, event, etc.
    description: str = ""
    properties: dict = Field(default_factory=dict)


class EntityCreate(EntityBase):
    pass


class EntityUpdate(BaseModel):
    name: str | None = None
    entity_type: str | None = None
    description: str | None = None
    properties: dict | None = None


class EntityResponse(EntityBase):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RelationBase(BaseModel):
    source_entity_id: UUID
    target_entity_id: UUID
    relation_type: str
    properties: dict = Field(default_factory=dict)


class RelationCreate(RelationBase):
    pass


class RelationResponse(RelationBase):
    id: UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class GraphNode(BaseModel):
    id: UUID
    label: str
    node_type: str
    properties: dict = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: UUID
    target: UUID
    relation_type: str
    properties: dict = Field(default_factory=dict)


class GraphData(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
