from uuid import UUID

from src.database.postgres import pg_pool
from src.models.entity import GraphData, GraphNode, GraphEdge, RelationCreate


async def create_relation(data: RelationCreate) -> dict:
    row = await pg_pool.fetchrow(
        "INSERT INTO relations (source_entity_id, target_entity_id, relation_type, properties) "
        "VALUES ($1, $2, $3, $4) RETURNING *",
        data.source_entity_id,
        data.target_entity_id,
        data.relation_type,
        data.properties,
    )
    return dict(row)


async def get_graph(user_id: UUID, depth: int = 2, entity_id: UUID | None = None) -> GraphData:
    if entity_id:
        # BFS from a specific entity
        visited_entities = set()
        visited_relations = set()
        queue = [(entity_id, 0)]
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
                    node_type=entity["entity_type"],
                    properties=entity["properties"],
                ))
                # Get connected entities
                relations = await pg_pool.fetch(
                    "SELECT * FROM relations WHERE source_entity_id = $1 OR target_entity_id = $1",
                    current_id,
                )
                for rel in relations:
                    rel_id = rel["id"]
                    if rel_id not in visited_relations:
                        visited_relations.add(rel_id)
                        target = rel["target_entity_id"] if rel["source_entity_id"] == current_id else rel["source_entity_id"]
                        edges.append(GraphEdge(
                            source=rel["source_entity_id"],
                            target=rel["target_entity_id"],
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
        nodes = [GraphNode(id=e["id"], label=e["name"], node_type=e["entity_type"], properties=e["properties"]) for e in entities]

        relations = await pg_pool.fetch(
            "SELECT * FROM relations WHERE source_entity_id = ANY($1) AND target_entity_id = ANY($1)",
            list(entity_ids),
        )
        edges = [GraphEdge(source=r["source_entity_id"], target=r["target_entity_id"], relation_type=r["relation_type"], properties=r["properties"]) for r in relations]

    return GraphData(nodes=nodes, edges=edges)
