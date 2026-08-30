"""Feedback module — knowledge extraction from AI interactions."""

from datetime import datetime
from pydantic import BaseModel, Field


class FeedbackExtractRequest(BaseModel):
    """Request to extract knowledge from a conversation."""
    conversation: str = Field(..., description="对话文本内容")
    source: str = Field(default="hermes", description="来源标识")
    session_id: str | None = Field(default=None, description="会话ID")


class FeedbackEntry(BaseModel):
    """A single extracted knowledge entry."""
    type: str = Field(..., description="knowledge | pattern | decision")
    title: str = Field(..., description="一句话标题")
    content: str = Field(..., description="详细内容")
    confidence: str = Field(default="medium", description="high | medium | low")
    evidence: str = Field(default="", description="对话中的关键证据")
    tags: list[str] = Field(default_factory=list)


class FeedbackExtractResponse(BaseModel):
    """Response from extraction."""
    entries: list[FeedbackEntry] = Field(default_factory=list)
    stored_count: int = 0
    skipped_count: int = 0


class FeedbackListResponse(BaseModel):
    """List feedback entries."""
    id: str
    user_id: str
    title: str
    content: str
    feedback_type: str = ""
    confidence: str = ""
    evidence: str = ""
    source: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""
