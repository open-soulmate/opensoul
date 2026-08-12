from uuid import UUID

from fastapi import APIRouter, HTTPException

from src.models.tag import TagCreate, TagUpdate, TagResponse
from src.database.postgres import db_pool

router = APIRouter()


@router.post("/", response_model=TagResponse)
async def create(data: TagCreate, user_id: UUID):
    row = await db_pool.fetchrow(
        "INSERT INTO tags (name, color, user_id) VALUES ($1, $2, $3) RETURNING *",
        data.name, data.color, user_id,
    )
    return dict(row)


@router.get("/", response_model=list[TagResponse])
async def list_all(user_id: UUID):
    rows = await db_pool.fetch(
        "SELECT t.*, COUNT(kt.knowledge_id) as usage_count FROM tags t "
        "LEFT JOIN knowledge_tags kt ON t.id = kt.tag_id "
        "WHERE t.user_id = $1 GROUP BY t.id ORDER BY t.name",
        user_id,
    )
    return [dict(r) for r in rows]


@router.patch("/{tag_id}", response_model=TagResponse)
async def update(tag_id: UUID, data: TagUpdate, user_id: UUID):
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        row = await db_pool.fetchrow("SELECT * FROM tags WHERE id = $1 AND user_id = $2", tag_id, user_id)
        if not row:
            raise HTTPException(status_code=404, detail="Tag not found")
        return dict(row)

    set_parts = []
    values = []
    idx = 1
    for field, value in fields.items():
        set_parts.append(f"{field} = ${idx}")
        values.append(value)
        idx += 1
    values.extend([tag_id, user_id])
    row = await db_pool.fetchrow(
        f"UPDATE tags SET {', '.join(set_parts)} WHERE id = ${idx} AND user_id = ${idx + 1} RETURNING *",
        *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Tag not found")
    return dict(row)


@router.delete("/{tag_id}")
async def delete(tag_id: UUID, user_id: UUID):
    result = await db_pool.execute("DELETE FROM tags WHERE id = $1 AND user_id = $2", tag_id, user_id)
    if "DELETE 1" not in result:
        raise HTTPException(status_code=404, detail="Tag not found")
    return {"deleted": True}
