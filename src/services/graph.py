from uuid import UUID, uuid4

from src.database.postgres import db_pool
from src.models.entity import GraphData, GraphNode, GraphEdge, RelationCreate
from src.services.extraction import extract_entities_and_relations


def _row_to_relation(row) -> dict:
    d = dict(row)
    d["source_entity_id"] = d.pop("source_id")
    d["target_entity_id"] = d.pop("target_id")
    return d


async def create_relation(data: RelationCreate) -> dict:
    row = await db_pool.fetchrow(
        "INSERT INTO relations (source_id, target_id, relation_type, properties) "
        "VALUES ($1, $2, $3, $4) RETURNING *",
        data.source_entity_id,
        data.target_entity_id,
        data.relation_type,
        data.properties,
    )
    return _row_to_relation(row)


async def list_relations(user_id: UUID, offset: int = 0, limit: int = 100) -> list[dict]:
    rows = await db_pool.fetch(
        "SELECT r.* FROM relations r "
        "JOIN entities e ON r.source_id = e.id "
        "WHERE e.user_id = $1 "
        "ORDER BY r.created_at DESC OFFSET $2 LIMIT $3",
        user_id, offset, limit,
    )
    return [_row_to_relation(r) for r in rows]


async def get_graph(user_id: UUID, depth: int = 2, entity_id: UUID | None = None) -> GraphData:
    if entity_id:
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

            entity = await db_pool.fetchrow(
                "SELECT * FROM entities WHERE id = $1 AND user_id = $2", current_id, user_id
            )
            if entity:
                nodes.append(GraphNode(
                    id=entity["id"],
                    label=entity["name"],
                    node_type=entity["type"],
                    properties=entity["properties"],
                ))
                relations = await db_pool.fetch(
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
        entities = await db_pool.fetch(
            "SELECT * FROM entities WHERE user_id = $1 ORDER BY name LIMIT 200", user_id
        )
        entity_ids = {e["id"] for e in entities}
        nodes = [GraphNode(id=e["id"], label=e["name"], node_type=e["type"], properties=e["properties"]) for e in entities]

        if entity_ids:
            relations = await db_pool.fetch(
                "SELECT * FROM relations WHERE source_id = ANY($1) AND target_id = ANY($1)",
                list(entity_ids),
            )
            edges = [GraphEdge(source=r["source_id"], target=r["target_id"], relation_type=r["relation_type"], properties=r["properties"]) for r in relations]
        else:
            edges = []

    return GraphData(nodes=nodes, edges=edges)


# ---------------------------------------------------------------------------
# Knowledge graph construction from NER results
# ---------------------------------------------------------------------------

async def build_graph_from_text(text: str, user_id: UUID) -> GraphData:
    """Extract entities and relations from text and build the knowledge graph."""
    extracted = await extract_entities_and_relations(text)

    # Resolve or create entities
    entity_name_to_id: dict[str, UUID] = {}
    for ent in extracted.get("entities", []):
        name = ent["name"].strip()
        if not name:
            continue

        existing = await db_pool.fetchrow(
            "SELECT id FROM entities WHERE user_id = $1 AND name = $2",
            user_id, name,
        )
        if existing:
            entity_name_to_id[name] = existing["id"]
        else:
            entity_id = uuid4()
            await db_pool.execute(
                "INSERT INTO entities (id, user_id, name, type, description, properties) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                entity_id,
                user_id,
                name,
                ent.get("type", "other"),
                ent.get("description", ""),
                {},
            )
            entity_name_to_id[name] = entity_id

    # Create relations
    for rel in extracted.get("relations", []):
        source_name = rel["source"].strip()
        target_name = rel["target"].strip()
        source_id = entity_name_to_id.get(source_name)
        target_id = entity_name_to_id.get(target_name)

        if not source_id or not target_id:
            continue

        # Skip duplicate relations
        exists = await db_pool.fetchrow(
            "SELECT id FROM relations WHERE source_id = $1 AND target_id = $2 AND relation_type = $3",
            source_id, target_id, rel.get("relation_type", "related"),
        )
        if not exists:
            await db_pool.execute(
                "INSERT INTO relations (id, source_id, target_id, relation_type, properties) "
                "VALUES ($1, $2, $3, $4, $5)",
                uuid4(),
                source_id,
                target_id,
                rel.get("relation_type", "related"),
                {},
            )

    return await get_graph(user_id)


# ---------------------------------------------------------------------------
# Graph query: entity neighbors and path analysis
# ---------------------------------------------------------------------------

async def get_entity_neighbors(user_id: UUID, entity_id: UUID) -> dict:
    """Get all directly connected entities and their relation types."""
    entity = await db_pool.fetchrow(
        "SELECT * FROM entities WHERE id = $1 AND user_id = $2", entity_id, user_id
    )
    if not entity:
        return {"entity": None, "neighbors": []}

    relations = await db_pool.fetch(
        "SELECT * FROM relations WHERE source_id = $1 OR target_id = $1", entity_id
    )
    neighbors = []
    for rel in relations:
        neighbor_id = rel["target_id"] if rel["source_id"] == entity_id else rel["source_id"]
        direction = "outgoing" if rel["source_id"] == entity_id else "incoming"
        neighbor = await db_pool.fetchrow(
            "SELECT id, name, type FROM entities WHERE id = $1", neighbor_id
        )
        if neighbor:
            neighbors.append({
                "entity_id": neighbor["id"],
                "name": neighbor["name"],
                "type": neighbor["type"],
                "relation_type": rel["relation_type"],
                "direction": direction,
            })

    return {
        "entity": {"id": entity["id"], "name": entity["name"], "type": entity["type"]},
        "neighbors": neighbors,
    }


async def find_path(user_id: UUID, source_id: UUID, target_id: UUID, max_depth: int = 5) -> list[list[dict]]:
    """Find all paths between two entities using BFS (up to max_depth)."""
    paths: list[list[dict]] = []
    queue: list[tuple[UUID, list[dict]]] = [(source_id, [{"entity_id": source_id}])]

    while queue:
        current_id, path = queue.pop(0)

        if len(path) > max_depth:
            continue

        if current_id == target_id and len(path) > 1:
            paths.append(path)
            continue

        visited_in_path = {step["entity_id"] for step in path}

        relations = await db_pool.fetch(
            "SELECT * FROM relations WHERE source_id = $1 OR target_id = $1", current_id
        )
        for rel in relations:
            neighbor_id = rel["target_id"] if rel["source_id"] == current_id else rel["source_id"]
            if neighbor_id not in visited_in_path:
                neighbor = await db_pool.fetchrow(
                    "SELECT id, name, type FROM entities WHERE id = $1", neighbor_id
                )
                if neighbor:
                    new_step = {
                        "entity_id": neighbor["id"],
                        "name": neighbor["name"],
                        "type": neighbor["type"],
                        "via_relation": rel["relation_type"],
                    }
                    queue.append((neighbor_id, path + [new_step]))

    return paths[:10]  # Cap at 10 paths
