import json
from uuid import UUID

from src.database.postgres import db_pool
from src.database.qdrant import qdrant_client
from src.database.meilisearch import meili_client

try:
    from qdrant_client.models import PointStruct
except ImportError:
    PointStruct = None
from src.models.knowledge import KnowledgeCreate, KnowledgeUpdate
from src.services.chunking import smart_chunk
from src.services.extraction import extract_text_from_file
from src.services.embedding import get_embeddings_batch


async def create_knowledge(data: KnowledgeCreate, user_id: UUID) -> dict:
    import uuid as _uuid
    knowledge_id = str(_uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO knowledge (id, title, content, source, content_type, metadata, user_id) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
        knowledge_id, data.title, data.content, data.source, data.content_type, json.dumps(data.metadata or {}), str(user_id),
    )
    row = await db_pool.fetchrow("SELECT * FROM knowledge WHERE id = $1", knowledge_id)

    # Add tags
    for tag_name in data.tags:
        tag_id = str(_uuid.uuid4())
        await db_pool.execute(
            "INSERT INTO tags (id, name, user_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            tag_id, tag_name, str(user_id),
        )
        tag = await db_pool.fetchrow(
            "SELECT id FROM tags WHERE name = $1 AND user_id = $2", tag_name, str(user_id)
        )
        if tag:
            await db_pool.execute(
                "INSERT INTO knowledge_tags (knowledge_id, tag_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                knowledge_id,
                tag["id"],
            )

    # Chunk and embed
    chunks = smart_chunk(data.content, content_type=data.content_type)
    if chunks:
        chunk_texts = [c.content for c in chunks]
        embeddings = await get_embeddings_batch(chunk_texts)
        points = []
        for chunk, embedding in zip(chunks, embeddings):
            point_id = f"{knowledge_id}_{chunk.index}"
            if PointStruct is not None:
                points.append(PointStruct(
                    id=point_id,
                    vector=embedding,
                    payload={
                        "knowledge_id": str(knowledge_id),
                        "chunk_index": chunk.index,
                        "content": chunk.content,
                        "user_id": str(user_id),
                    },
                ))
            chunk_id = str(_uuid.uuid4())
            await db_pool.execute(
                "INSERT INTO knowledge_chunks (id, knowledge_id, chunk_index, content, embedding_id, token_count) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                chunk_id, knowledge_id, chunk.index, chunk.content, point_id, len(chunk.content.split()),
            )
        if points:
            qdrant_client.upsert_points(points)

    # Index in Meilisearch (always, even if no chunks)
    if meili_client.AVAILABLE:
        meili_client.add_documents([{
            "id": str(knowledge_id),
            "title": data.title,
            "content": data.content[:5000],
            "tags": data.tags,
            "user_id": str(user_id),
            "content_type": data.content_type,
        }])

    d = dict(row)
    if isinstance(d.get("metadata"), str):
        try: d["metadata"] = json.loads(d["metadata"])
        except: d["metadata"] = {}
    return d


async def get_knowledge(knowledge_id: UUID, user_id: UUID) -> dict | None:
    row = await db_pool.fetchrow(
        "SELECT k.*, GROUP_CONCAT(t.name) as tags "
        "FROM knowledge k "
        "LEFT JOIN knowledge_tags kt ON k.id = kt.knowledge_id "
        "LEFT JOIN tags t ON kt.tag_id = t.id "
        "WHERE k.id = $1 AND k.user_id = $2 GROUP BY k.id",
        knowledge_id,
        str(user_id),
    )
    d = dict(row)
    if isinstance(d.get("metadata"), str):
        try: d["metadata"] = json.loads(d["metadata"])
        except: d["metadata"] = {}
    return d if row else None


async def list_knowledge(
    user_id: UUID,
    offset: int = 0,
    limit: int = 20,
    content_type: str | None = None,
    domain: str | None = None,
    tag: str | None = None,
) -> list[dict]:
    conditions = ["k.user_id = ?"]
    values: list = [str(user_id)]
    idx = 2

    if content_type:
        conditions.append(f"k.content_type = ?")
        values.append(content_type)
        idx += 1

    if domain:
        conditions.append(f"k.metadata IS NOT NULL")
        values.append(domain)
        idx += 1

    where_clause = " AND ".join(conditions)

    if tag:
        # Filter by tag via join
        query = (
            "SELECT k.* FROM knowledge k "
            "WHERE " + where_clause + " AND EXISTS ("
            "SELECT 1 FROM knowledge_tags kt2 JOIN tags t2 ON kt2.tag_id = t2.id "
            "WHERE kt2.knowledge_id = k.id AND t2.name = ?) "
            "ORDER BY k.created_at DESC LIMIT ? OFFSET ?"
        )
        values.extend([tag, limit, offset])
    else:
        query = (
            "SELECT k.* FROM knowledge k "
            "WHERE " + where_clause + " "
            "ORDER BY k.created_at DESC LIMIT ? OFFSET ?"
        )
        values.extend([limit, offset])

    rows = await db_pool.fetch(query, *values)
    result = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("metadata"), str):
            try: d["metadata"] = json.loads(d["metadata"])
            except: d["metadata"] = {}
        result.append(d)
    return result


async def update_knowledge(knowledge_id: UUID, data: KnowledgeUpdate, user_id: UUID) -> dict | None:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_knowledge(knowledge_id, str(user_id))

    set_clauses = []
    values = []
    idx = 1
    for field, value in fields.items():
        if field != "tags":
            set_clauses.append(f"{field} = ?")
            values.append(value)
            idx += 1

    values.extend([knowledge_id, str(user_id)])
    await db_pool.execute(
        f"UPDATE knowledge SET {', '.join(set_clauses)}, updated_at = NOW() "
        f"WHERE id = ? AND user_id = ?",
        *values,
    )

    return await get_knowledge(knowledge_id, str(user_id))


async def delete_knowledge(knowledge_id: UUID, user_id: UUID) -> bool:
    # Delete chunks from qdrant
    chunks = await db_pool.fetch(
        "SELECT embedding_id FROM knowledge_chunks WHERE knowledge_id = $1", knowledge_id
    )
    embedding_ids = [c["embedding_id"] for c in chunks if c["embedding_id"]]
    if embedding_ids and qdrant_client.AVAILABLE:
        qdrant_client.delete_points(embedding_ids)

    # Delete from meilisearch
    if meili_client.AVAILABLE:
        meili_client.delete_document(str(knowledge_id))

    # Delete from postgres
    result = await db_pool.execute(
        "DELETE FROM knowledge WHERE id = $1 AND user_id = $2", knowledge_id, user_id
    )
    return "DELETE 1" in result


async def toggle_star(knowledge_id: UUID, user_id: UUID) -> dict | None:
    """Toggle star status on a knowledge item."""
    row = await db_pool.fetchrow(
        "UPDATE knowledge SET starred = NOT COALESCE(starred, FALSE), updated_at = NOW() "
        "WHERE id = $1 AND user_id = $2 RETURNING starred",
        knowledge_id, str(user_id),
    )
    if not row:
        return None
    return {"starred": row["starred"]}


async def toggle_pin(knowledge_id: UUID, user_id: UUID) -> dict | None:
    """Toggle pin status on a knowledge item."""
    row = await db_pool.fetchrow(
        "UPDATE knowledge SET pinned = NOT COALESCE(pinned, FALSE), updated_at = NOW() "
        "WHERE id = $1 AND user_id = $2 RETURNING pinned",
        knowledge_id, str(user_id),
    )
    if not row:
        return None
    return {"pinned": row["pinned"]}
