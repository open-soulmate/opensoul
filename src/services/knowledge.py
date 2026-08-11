from uuid import UUID

from qdrant_client.models import PointStruct

from src.database.postgres import pg_pool
from src.database.qdrant import qdrant_client
from src.database.meilisearch import meili_client
from src.models.knowledge import KnowledgeCreate, KnowledgeUpdate
from src.services.chunking import chunk_text
from src.services.embedding import get_embedding, get_embeddings_batch
from src.services.extraction import extract_entities_and_relations


async def create_knowledge(data: KnowledgeCreate, user_id: UUID) -> dict:
    row = await pg_pool.fetchrow(
        "INSERT INTO knowledge (title, content, source, content_type, metadata, user_id) "
        "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
        data.title,
        data.content,
        data.source,
        data.content_type,
        data.metadata,
        user_id,
    )
    knowledge_id = row["id"]

    # Add tags
    for tag_name in data.tags:
        await pg_pool.execute(
            "INSERT INTO tags (name, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            tag_name,
            user_id,
        )
        tag = await pg_pool.fetchrow(
            "SELECT id FROM tags WHERE name = $1 AND user_id = $2", tag_name, user_id
        )
        if tag:
            await pg_pool.execute(
                "INSERT INTO knowledge_tags (knowledge_id, tag_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                knowledge_id,
                tag["id"],
            )

    # Chunk and embed
    chunks = chunk_text(data.content)
    if chunks:
        embeddings = await get_embeddings_batch(chunks)
        points = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            point_id = f"{knowledge_id}_{i}"
            points.append(PointStruct(id=point_id, vector=embedding, payload={"knowledge_id": str(knowledge_id), "chunk_index": i, "content": chunk}))
            await pg_pool.execute(
                "INSERT INTO knowledge_chunks (knowledge_id, chunk_index, content, embedding_id, token_count) "
                "VALUES ($1, $2, $3, $4, $5)",
                knowledge_id, i, chunk, point_id, len(chunk.split()),
            )
        qdrant_client.upsert_points(points)

        # Index first chunk in Meilisearch
        meili_client.add_documents([{
            "id": str(knowledge_id),
            "title": data.title,
            "content": data.content[:5000],
            "tags": data.tags,
            "user_id": str(user_id),
        }])

    return dict(row)


async def get_knowledge(knowledge_id: UUID, user_id: UUID) -> dict | None:
    row = await pg_pool.fetchrow(
        "SELECT k.*, array_agg(t.name) as tags FROM knowledge k "
        "LEFT JOIN knowledge_tags kt ON k.id = kt.knowledge_id "
        "LEFT JOIN tags t ON kt.tag_id = t.id "
        "WHERE k.id = $1 AND k.user_id = $2 GROUP BY k.id",
        knowledge_id,
        user_id,
    )
    return dict(row) if row else None


async def list_knowledge(user_id: UUID, offset: int = 0, limit: int = 20) -> list[dict]:
    rows = await pg_pool.fetch(
        "SELECT k.*, array_agg(t.name) as tags FROM knowledge k "
        "LEFT JOIN knowledge_tags kt ON k.id = kt.knowledge_id "
        "LEFT JOIN tags t ON kt.tag_id = t.id "
        "WHERE k.user_id = $1 GROUP BY k.id "
        "ORDER BY k.created_at DESC OFFSET $2 LIMIT $3",
        user_id,
        offset,
        limit,
    )
    return [dict(r) for r in rows]


async def update_knowledge(knowledge_id: UUID, data: KnowledgeUpdate, user_id: UUID) -> dict | None:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_knowledge(knowledge_id, user_id)

    set_clauses = []
    values = []
    idx = 1
    for field, value in fields.items():
        if field != "tags":
            set_clauses.append(f"{field} = ${idx}")
            values.append(value)
            idx += 1

    values.extend([knowledge_id, user_id])
    await pg_pool.execute(
        f"UPDATE knowledge SET {', '.join(set_clauses)}, updated_at = NOW() "
        f"WHERE id = ${idx} AND user_id = ${idx + 1}",
        *values,
    )

    return await get_knowledge(knowledge_id, user_id)


async def delete_knowledge(knowledge_id: UUID, user_id: UUID) -> bool:
    # Delete chunks from qdrant
    chunks = await pg_pool.fetch(
        "SELECT embedding_id FROM knowledge_chunks WHERE knowledge_id = $1", knowledge_id
    )
    embedding_ids = [c["embedding_id"] for c in chunks if c["embedding_id"]]
    if embedding_ids:
        qdrant_client.delete_points(embedding_ids)

    # Delete from meilisearch
    meili_client.delete_document(str(knowledge_id))

    # Delete from postgres
    result = await pg_pool.execute(
        "DELETE FROM knowledge WHERE id = $1 AND user_id = $2", knowledge_id, user_id
    )
    return "DELETE 1" in result
