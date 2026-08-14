from uuid import UUID

from src.database.postgres import db_pool
from src.models.entity import EntityCreate, EntityUpdate

# DB column mapping: the entities table uses "type" (not "entity_type").
# We rename on read so the API contract stays `entity_type`.


def _row_to_entity(row) -> dict:
    d = dict(row)
    d["entity_type"] = d.pop("type", "")
    return d


async def create_entity(data: EntityCreate, user_id: UUID) -> dict:
    import uuid as _uuid
    entity_id = str(_uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO entities (id, name, type, description, properties, user_id) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        entity_id, data.name, data.entity_type, data.description, data.properties, str(user_id),
    )
    row = await db_pool.fetchrow("SELECT * FROM entities WHERE id = $1", entity_id)
    return _row_to_entity(row)


async def get_entity(entity_id: UUID, user_id: UUID) -> dict | None:
    row = await db_pool.fetchrow(
        "SELECT * FROM entities WHERE id = $1 AND user_id = $2", entity_id, user_id
    )
    return _row_to_entity(row) if row else None


async def get_entity_with_relations(entity_id: UUID, user_id: UUID) -> dict | None:
    entity = await get_entity(entity_id, user_id)
    if not entity:
        return None
    relations = await db_pool.fetch(
        "SELECT r.*, se.name AS source_name, te.name AS target_name "
        "FROM relations r "
        "JOIN entities se ON r.source_id = se.id "
        "JOIN entities te ON r.target_id = te.id "
        "WHERE r.source_id = $1 OR r.target_id = $1 "
        "ORDER BY r.created_at",
        entity_id,
    )
    entity["relations"] = [dict(r) for r in relations]
    return entity


async def list_entities(user_id: UUID, entity_type: str | None = None, offset: int = 0, limit: int = 50) -> list[dict]:
    if entity_type:
        rows = await db_pool.fetch(
            "SELECT * FROM entities WHERE user_id = $1 AND type = $2 "
            "ORDER BY name LIMIT $4 OFFSET $3",
            str(user_id), entity_type, offset, limit,
        )
    else:
        rows = await db_pool.fetch(
            "SELECT * FROM entities WHERE user_id = $1 ORDER BY name LIMIT $3 OFFSET $2",
            str(user_id), offset, limit,
        )
    return [_row_to_entity(r) for r in rows]


async def update_entity(entity_id: UUID, data: EntityUpdate, user_id: UUID) -> dict | None:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_entity(entity_id, user_id)

    # Map API field `entity_type` back to DB column `type`
    set_clauses = []
    values = []
    idx = 1
    for field, value in fields.items():
        col = "type" if field == "entity_type" else field
        set_clauses.append(f"{col} = ${idx}")
        values.append(value)
        idx += 1

    values.extend([entity_id, user_id])
    await db_pool.execute(
        f"UPDATE entities SET {', '.join(set_clauses)}, updated_at = NOW() "
        f"WHERE id = ${idx} AND user_id = ${idx + 1}",
        *values,
    )
    return await get_entity(entity_id, user_id)


async def delete_entity(entity_id: UUID, user_id: UUID) -> bool:
    result = await db_pool.execute(
        "DELETE FROM entities WHERE id = $1 AND user_id = $2", entity_id, user_id
    )
    return "DELETE 1" in result
