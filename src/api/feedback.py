"""Feedback API — extract and query knowledge from AI interactions."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from src.models.feedback import (
    FeedbackExtractRequest,
    FeedbackExtractResponse,
    FeedbackListResponse,
)
from src.services import feedback as feedback_service

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "component": "Feedback"}


@router.post("/extract", response_model=FeedbackExtractResponse)
async def extract_knowledge(
    data: FeedbackExtractRequest,
    user_id: UUID = Query(..., description="User ID"),
):
    """Extract knowledge from a conversation and store it.

    Sends the conversation text to the extraction pipeline (rule-based + optional LLM),
    then stores the results in the knowledge table with feedback metadata.
    """
    try:
        # Extract entries
        entries = await feedback_service.extract_knowledge(data)

        if not entries:
            return FeedbackExtractResponse(entries=[], stored_count=0, skipped_count=0)

        # Store entries
        stored, skipped = await feedback_service.store_entries(
            entries=entries,
            user_id=str(user_id),
            source=data.source,
            session_id=data.session_id,
        )

        return FeedbackExtractResponse(
            entries=entries,
            stored_count=stored,
            skipped_count=skipped,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/entries", response_model=list[FeedbackListResponse])
async def list_entries(
    user_id: UUID = Query(..., description="User ID"),
    type: str | None = Query(None, description="Filter by type: knowledge|pattern|decision"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List feedback entries for a user."""
    try:
        entries = await feedback_service.list_entries(
            user_id=str(user_id),
            feedback_type=type,
            limit=limit,
            offset=offset,
        )
        return entries
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def feedback_stats(
    user_id: UUID = Query(..., description="User ID"),
):
    """Get feedback statistics."""
    from src.database.postgres import db_pool

    try:
        total = (
            await db_pool.fetchval(
                "SELECT COUNT(*) FROM knowledge WHERE user_id = ? AND content_type = 'feedback'",
                str(user_id),
            )
            or 0
        )

        by_type = await db_pool.fetch(
            "SELECT json_extract(metadata, '$.feedback_type') as type, COUNT(*) as cnt "
            "FROM knowledge WHERE user_id = ? AND content_type = 'feedback' "
            "GROUP BY json_extract(metadata, '$.feedback_type')",
            str(user_id),
        )

        by_confidence = await db_pool.fetch(
            "SELECT json_extract(metadata, '$.confidence') as conf, COUNT(*) as cnt "
            "FROM knowledge WHERE user_id = ? AND content_type = 'feedback' "
            "GROUP BY json_extract(metadata, '$.confidence')",
            str(user_id),
        )

        return {
            "status": "ok",
            "total": total,
            "by_type": {r["type"]: r["cnt"] for r in by_type if r["type"]},
            "by_confidence": {r["conf"]: r["cnt"] for r in by_confidence if r["conf"]},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
