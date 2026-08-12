from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.database.postgres import db_pool

router = APIRouter()


@router.get("/json")
async def export_json(user_id: UUID):
    """Export all user data as JSON."""
    knowledge = await db_pool.fetch(
        "SELECT * FROM knowledge WHERE user_id = $1 ORDER BY created_at", user_id
    )
    entities = await db_pool.fetch(
        "SELECT * FROM entities WHERE user_id = $1 ORDER BY name", user_id
    )
    tags = await db_pool.fetch(
        "SELECT * FROM tags WHERE user_id = $1 ORDER BY name", user_id
    )

    return JSONResponse({
        "knowledge": [dict(r) for r in knowledge],
        "entities": [dict(r) for r in entities],
        "tags": [dict(r) for r in tags],
    })


@router.get("/markdown")
async def export_markdown(user_id: UUID):
    """Export all knowledge as JSON with markdown-formatted content."""
    knowledge = await db_pool.fetch(
        "SELECT k.*, COALESCE(array_agg(t.name) FILTER (WHERE t.name IS NOT NULL), '{}') as tags "
        "FROM knowledge k "
        "LEFT JOIN knowledge_tags kt ON k.id = kt.knowledge_id "
        "LEFT JOIN tags t ON kt.tag_id = t.id "
        "WHERE k.user_id = $1 GROUP BY k.id ORDER BY k.created_at",
        user_id,
    )
    entities = await db_pool.fetch(
        "SELECT * FROM entities WHERE user_id = $1 ORDER BY name", user_id
    )
    tags = await db_pool.fetch(
        "SELECT * FROM tags WHERE user_id = $1 ORDER BY name", user_id
    )

    return JSONResponse({
        "knowledge": [dict(r) for r in knowledge],
        "entities": [dict(r) for r in entities],
        "tags": [dict(r) for r in tags],
        "format": "markdown",
    })
