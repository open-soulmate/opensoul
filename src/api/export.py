from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.database.postgres import pg_pool

router = APIRouter()


@router.get("/json")
async def export_json(user_id: UUID):
    """Export all user data as JSON."""
    knowledge = await pg_pool.fetch(
        "SELECT * FROM knowledge WHERE user_id = $1 ORDER BY created_at", user_id
    )
    entities = await pg_pool.fetch(
        "SELECT * FROM entities WHERE user_id = $1 ORDER BY name", user_id
    )
    tags = await pg_pool.fetch(
        "SELECT * FROM tags WHERE user_id = $1 ORDER BY name", user_id
    )

    return JSONResponse({
        "knowledge": [dict(r) for r in knowledge],
        "entities": [dict(r) for r in entities],
        "tags": [dict(r) for r in tags],
    })
