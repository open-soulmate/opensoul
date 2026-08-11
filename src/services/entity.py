from uuid import UUID

from src.database.postgres import pg_pool
from src.models.entity import EntityCreate, EntityUpdate


async def create_entity(data: EntityCreate, user_id: UUID) -> dict:
    row = await pg_pool.fetchrow(
        "INSERT INTO entities (name, entity_type, description, properties, user_id) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING *",
        data.name,
        data.entity_type,
        data.description,
        data.properties,
        user_id,
    )
    return dict(row)


async def get_entity(entity_id: UUID, user_id: UUID) -> dict | None:
    row = await pg_pool.fetchrow(
        "SELECT * FROM entities WHERE id = $1 AND user_id = $2", entity_id, user_id
    )
    return dict(row) if row else None


async def list_entities(user_id: UUID, entity_type: str | None = None, offset: int = 0, limit: int = 50) -> list[dict]:
    if entity_type:
        rows = await pg_pool.fetch(
            "SELECT * FROM entities WHERE user_id = $1 AND entity_type = $2 "
            "ORDER BY name OFFSET $3 LIMIT $4",
            user_id, entity_type, offset, limit,
        )
    else:
        rows = await pg_pool.fetch(
            "SELECT * FROM entities WHERE user_id = $1 ORDER BY name OFFSET $2 LIMIT $3",
            user_id, offset, limit,
        )
    return [dict(r) for r in rows]


async def update_entity(entity_id: UUID, data: EntityUpdate, user_id: UUID) -> dict | None:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_entity(entity_id, user_id)

    set_clauses = []
    values = []
    idx = 1
    for field, value in fields.items():
        set_clauses.append(f"{field} = ${idx}")
        values.append(value)
        idx += 1

    values.extend([entity_id, user_id])
    await pg_pool.execute(
        f"UPDATE entities SET {', '.join(set_clauses)}, updated_at = NOW() "
        f"WHERE id = ${idx} AND user_id = ${idx + 1}",
        *values,
    )
    return await get_entity(entity_id, user_id)


async def delete_entity(entity_id: UUID, user_id: UUID) -> bool:
    result = await pg_pool.execute(
        "DELETE FROM entities WHERE id = $1 AND user_id = $2", entity_id, user_id
    )
    return "DELETE 1" in result
