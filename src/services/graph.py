from uuid import UUID

from src.database.postgres import pg_pool
from src.models.entity import GraphData, GraphNode, GraphEdge, RelationCreate


def _row_to_relation(row) -> dict:
    d = dict(row)
    # DB columns are source_id / target_id; API contract uses source_entity_id / target_entity_id
    d["source_entity_id"] = d.pop("source_id")
    d["target_entity_id"] = d.pop("target_id")
    return d


async def create_relation(data: RelationCreate) -> dict:
    row = await pg_pool.fetchrow(
        "INSERT INTO relations (source_id, target_id, relation_type, properties) "
        "VALUES ($1, $2, $3, $4) RETURNING *",
        data.source_entity_id,
        data.target_entity_id,
        data.relation_type,
        data.properties,
    )
    return _row_to_relation(row)


async def list_relations(user_id: UUID, offset: int = 0, limit: int = 100) -> list[dict]:
    rows = await pg_pool.fetch(
        "SELECT r.* FROM relations r "
        "JOIN entities e ON r.source_id = e.id "
        "WHERE e.user_id = $1 "
        "ORDER BY r.created_at DESC OFFSET $2 LIMIT $3",
        user_id, offset, limit,
    )
    return [_row_to_relation(r) for r in rows]


async def get_graph(user_id: UUID, depth: int = 2, entity_id: UUID | None = None) -> GraphData:
    if entity_id:
        # BFS from a specific entity
        visited_entities: set[UUID] = set()
        visited_relations: set[UUID] = set()
        queue: list[tuple[UUID, int]] = [(entity_id, 0)]
        nodes = []
        edges = []

        while queue:
            current_id, current_depth = queue.pop(0)
            if current_id in visited_entities or current_depth > depth:
                continue
            visited_entities.add(current_id)

            entity = await pg_pool.fetchrow(
                "SELECT * FROM entities WHERE id = $1 AND user_id = $2", current_id, user_id
            )
            if entity:
                nodes.append(GraphNode(
                    id=entity["id"],
                    label=entity["name"],
                    node_type=entity["type"],
                    properties=entity["properties"],
                ))
                # Get connected entities
                relations = await pg_pool.fetch(
                    "SELECT * FROM relations WHERE source_id = $1 OR target_id = $1",
                    current_id,
                )
                for rel in relations:
                    rel_id = rel["id"]
                    if rel_id not in visited_relations:
                        visited_relations.add(rel_id)
                        target = rel["target_id"] if rel["source_id"] == current_id else rel["source_id"]
                        edges.append(GraphEdge(
                            source=rel["source_id"],
                            target=rel["target_id"],
                            relation_type=rel["relation_type"],
                            properties=rel["properties"],
                        ))
                        if current_depth + 1 <= depth:
                            queue.append((target, current_depth + 1))
    else:
        # Return all entities and relations for user
        entities = await pg_pool.fetch(
            "SELECT * FROM entities WHERE user_id = $1 ORDER BY name LIMIT 200", user_id
        )
        entity_ids = {e["id"] for e in entities}
        nodes = [GraphNode(id=e["id"], label=e["name"], node_type=e["type"], properties=e["properties"]) for e in entities]

        if entity_ids:
            relations = await pg_pool.fetch(
                "SELECT * FROM relations WHERE source_id = ANY($1) AND target_id = ANY($1)",
                list(entity_ids),
            )
            edges = [GraphEdge(source=r["source_id"], target=r["target_id"], relation_type=r["relation_type"], properties=r["properties"]) for r in relations]
        else:
            edges = []

    return GraphData(nodes=nodes, edges=edges)
