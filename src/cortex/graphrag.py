"""GraphRAG — Automatic entity/relation extraction from knowledge documents.

Builds knowledge graphs from document content using pattern-based NER
and relation extraction. Optionally augments with LLM-based extraction
via OpenGland.
"""

import json
import logging
import re
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# ── Entity Recognition Patterns ─────────────────────────────────────

ENTITY_PATTERNS: dict[str, list[str]] = {
    "person": [
        r"(?:Mr\.|Ms\.|Dr\.|Prof\.)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        r"(?<![a-zA-Z\u4e00-\u9fff])[\u4e00-\u9fff]{2,4}(?:先生|女士|博士|教授|老师)",
    ],
    "organization": [
        # Chinese org names: 2-6 chars + org suffix (negative lookbehind for particles)
        r"(?<![的了和与跟])[\u4e00-\u9fff]{2,6}(?:公司|集团|股份|科技|技术|信息|软件|网络|通信|大学|学院|研究院|研究所|医院|银行|基金|协会|联盟)",
        r"[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\s+(?:Inc|Corp|Ltd|LLC|Co|University|Institute|Foundation)",
    ],
    "product": [
        r"(?<![的了和与跟])[\u4e00-\u9fff]{2,6}(?:服务器|交换机|路由器|防火墙|存储|显示屏|LED|录播|投影|电脑|笔记本|工作站|打印机|手机|平板|耳机|摄像头)",
        r"(?:CPU|GPU|SSD|HDD|UPS|NAS|SAN|API|SDK|LLM|GPT|BERT|CLIP|LLaMA|Mistral|Qwen|DeepSeek)",
    ],
    "technology": [
        r"(?:Python|JavaScript|TypeScript|Rust|Go|Java|C\+\+|React|Vue|Next\.js|FastAPI|Docker|Kubernetes|Linux|Redis|PostgreSQL|MongoDB|Qdrant|NATS)",
        r"(?<![的了和与跟])[\u4e00-\u9fff]{2,6}(?:算法|框架|引擎|数据库|缓存|消息队列|微服务|容器|云原生|大模型|深度学习|机器学习)",
    ],
    "concept": [
        r"(?:RAG|GraphRAG|Agent|Transformer|Attention|Embedding|Vector|Fine-?tun(?:e|ing)|Prompt|Chain.of.Thought|CoT|RLHF|DPO)",
        r"(?<![的了和与跟])[\u4e00-\u9fff]{2,6}(?:架构|模式|策略|方案|方法|理论|模型|范式|机制|标准|规范)",
    ],
    "location": [
        r"(?:北京|上海|广州|深圳|杭州|成都|武汉|西安|南京|重庆|天津|苏州|长沙|郑州|青岛|大连|沈阳|厦门|昆明|合肥)",
        r"(?:San Francisco|New York|London|Tokyo|Singapore|Berlin|Paris|Beijing|Shanghai|Shenzhen)",
    ],
    "amount": [
        r"\d+(?:\.\d+)?\s*(?:万元|亿元|美元|EUR|USD|CNY)",
        r"\$\s*\d+(?:,\d{3})*(?:\.\d+)?",
    ],
}

# ── Relation Extraction Patterns ────────────────────────────────────

RELATION_PATTERNS: list[tuple[str, str]] = [
    # Chinese patterns
    (r"([\u4e00-\u9fffA-Za-z]+?)(?:是|为|属于|隶属于|旗下)([\u4e00-\u9fffA-Za-z]+)", "belongs_to"),
    (
        r"([\u4e00-\u9fffA-Za-z]+?)(?:生产|制造|提供|推出|发布|开发了?)([\u4e00-\u9fffA-Za-z]+)",
        "produces",
    ),
    (
        r"([\u4e00-\u9fffA-Za-z]+?)(?:使用|采用|部署|采购|选用|基于|依赖)([\u4e00-\u9fffA-Za-z]+)",
        "uses",
    ),
    (
        r"([\u4e00-\u9fffA-Za-z]+?)(?:竞品|竞争对手|替代|对标)([\u4e00-\u9fffA-Za-z]+)",
        "competes_with",
    ),
    (
        r"([\u4e00-\u9fffA-Za-z]+?)(?:合作|联合|共建|携手|集成)([\u4e00-\u9fffA-Za-z]+)",
        "cooperates_with",
    ),
    (r"([\u4e00-\u9fffA-Za-z]+?)(?:包含|含有|包括|涵盖|内置)([\u4e00-\u9fffA-Za-z]+)", "contains"),
    (r"([\u4e00-\u9fffA-Za-z]+?)(?:推荐|建议|首选|适用于)([\u4e00-\u9fffA-Za-z]+)", "recommends"),
    (r"([\u4e00-\u9fffA-Za-z]+?)(?:投资|收购|并购|入股)([\u4e00-\u9fffA-Za-z]+)", "invests_in"),
    (r"([\u4e00-\u9fffA-Za-z]+?)(?:位于|坐落在|总部在|设在)([\u4e00-\u9fffA-Za-z]+)", "located_in"),
    (r"([\u4e00-\u9fffA-Za-z]+?)(?:创建|发明|提出|研发)([\u4e00-\u9fffA-Za-z]+)", "created"),
    # English patterns
    (r"([A-Z][a-zA-Z\s]+?)\s+(?:is\s+a|is\s+an|are)\s+([a-zA-Z\s]+)", "is_a"),
    (
        r"([A-Z][a-zA-Z\s]+?)\s+(?:uses|uses|built\s+on|based\s+on|powered\s+by)\s+([A-Z][a-zA-Z\s]+)",
        "uses",
    ),
    (r"([A-Z][a-zA-Z\s]+?)\s+(?:includes|contains|supports)\s+([A-Z][a-zA-Z\s]+)", "contains"),
    (r"([A-Z][a-zA-Z\s]+?)\s+(?:competes\s+with|rival)\s+([A-Z][a-zA-Z\s]+)", "competes_with"),
    (r"([A-Z][a-zA-Z\s]+?)\s+(?:acquired|bought|invested\s+in)\s+([A-Z][a-zA-Z\s]+)", "invests_in"),
]


class GraphRAGEngine:
    """Extract entities and relations from text, build knowledge graphs."""

    def __init__(self):
        self._entity_patterns = ENTITY_PATTERNS
        self._relation_patterns = RELATION_PATTERNS
        self._particles = set(
            "的了和与跟但而或并也就又再还才被把将从在到对向给让过着吗呢吧啊呀哦嘛"
        )
        self._suffixes = [
            "公司",
            "集团",
            "股份",
            "科技",
            "技术",
            "信息",
            "软件",
            "网络",
            "通信",
            "大学",
            "学院",
            "研究院",
            "研究所",
            "服务器",
            "交换机",
            "路由器",
            "防火墙",
            "存储",
            "显示屏",
            "摄像头",
            "手机",
            "平板",
            "工作站",
            "投影",
            "打印机",
            "电脑",
            "笔记本",
            "银行",
            "医院",
            "基金",
            "协会",
            "联盟",
        ]

    # ── Entity Extraction ───────────────────────────────────────────

    def extract_entities(self, text: str) -> list[dict[str, Any]]:
        """Extract named entities from text using pattern matching."""
        entities: list[dict[str, Any]] = []
        seen: set[str] = set()

        for entity_type, patterns in self._entity_patterns.items():
            for pattern in patterns:
                try:
                    for m in re.finditer(pattern, text):
                        name = m.group(0).strip()
                        # Trim at last particle boundary
                        name = self._trim_at_particle(name)
                        # Filter noise
                        if len(name) < 2 or len(name) > 30:
                            continue
                        if name in seen:
                            continue
                        # Skip common words
                        if name.lower() in {
                            "the",
                            "a",
                            "an",
                            "is",
                            "are",
                            "was",
                            "were",
                            "in",
                            "on",
                            "at",
                            "of",
                            "to",
                            "for",
                        }:
                            continue
                        # Skip single-char names for non-location types
                        if len(name) < 2 and entity_type not in ("location",):
                            continue
                        seen.add(name)
                        entities.append(
                            {
                                "name": name,
                                "type": entity_type,
                                "properties": {},
                            }
                        )
                except re.error:
                    continue

        return entities

    def _trim_at_particle(self, name: str) -> str:
        """Trim an entity name at the last particle boundary.

        E.g. '并与锐捷科技' → '锐捷科技'
             '了海康威视的监控摄像头' → '监控摄像头'
        """
        if not name:
            return name

        # Find suffix keyword position
        suffix_start = -1
        for sfx in self._suffixes:
            idx = name.find(sfx)
            if idx >= 0:
                suffix_start = idx
                break

        if suffix_start > 0:
            prefix = name[:suffix_start]
            # Find last particle in prefix
            last_p = -1
            for i, c in enumerate(prefix):
                if c in self._particles:
                    last_p = i
            if last_p >= 0:
                name = prefix[last_p + 1 :] + name[suffix_start:]
        else:
            # No suffix keyword — just strip leading particles
            while name and name[0] in self._particles:
                name = name[1:]

        return name

    # ── Relation Extraction ─────────────────────────────────────────

    def extract_relations(self, text: str, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Extract relations between known entities from text."""
        relations: list[dict[str, Any]] = []
        entity_names = {e["name"] for e in entities}
        seen_pairs: set[tuple[str, str, str]] = set()

        for pattern, rel_type in self._relation_patterns:
            try:
                matches = re.findall(pattern, text)
                for match in matches:
                    source = match[0].strip()
                    target = match[1].strip()

                    # Only keep relations between known entities
                    if source not in entity_names or target not in entity_names:
                        continue
                    if source == target:
                        continue

                    pair_key = (source, target, rel_type)
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)

                    relations.append(
                        {
                            "source": source,
                            "target": target,
                            "type": rel_type,
                            "properties": {},
                        }
                    )
            except re.error:
                continue

        return relations

    # ── Build Graph from Knowledge ──────────────────────────────────

    async def build_graph_from_knowledge(self, db_pool: Any, user_id: str) -> dict[str, Any]:
        """Scan all knowledge entries for a user, extract entities/relations,
        and upsert into the entities/relations tables.

        Returns extraction statistics.
        """
        # Fetch all knowledge for this user
        rows = await db_pool.fetch(
            "SELECT id, title, content FROM knowledge WHERE user_id = $1",
            user_id,
        )

        all_entities: dict[str, dict] = {}
        all_relations: list[dict] = []

        for row in rows:
            text_parts = [row["title"] or ""]
            content = row["content"] or ""
            if content:
                text_parts.append(content[:10000])  # Limit per-doc

            text = " ".join(text_parts)
            if not text.strip():
                continue

            # Extract entities
            entities = self.extract_entities(text)
            for e in entities:
                name = e["name"]
                if name not in all_entities:
                    all_entities[name] = e
                # Merge properties
                for k, v in e.get("properties", {}).items():
                    if k not in all_entities[name].get("properties", {}):
                        all_entities[name].setdefault("properties", {})[k] = v

            # Extract relations
            relations = self.extract_relations(text, entities)
            all_relations.extend(relations)

        # Upsert entities
        entity_id_map: dict[str, str] = {}
        new_entity_count = 0
        import uuid as _uuid

        for name, entity in all_entities.items():
            existing = await db_pool.fetchrow(
                "SELECT id FROM entities WHERE name = $1 AND user_id = $2",
                name,
                user_id,
            )
            if existing:
                entity_id_map[name] = str(existing["id"])
            else:
                eid = str(_uuid.uuid4())
                await db_pool.execute(
                    "INSERT INTO entities (id, name, type, description, properties, user_id) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    eid,
                    name,
                    entity["type"],
                    "",
                    json.dumps(entity.get("properties", {})),
                    user_id,
                )
                entity_id_map[name] = eid
                new_entity_count += 1

        # Upsert relations
        new_relation_count = 0
        for rel in all_relations:
            source_id = entity_id_map.get(rel["source"])
            target_id = entity_id_map.get(rel["target"])
            if not source_id or not target_id:
                continue

            # Check duplicate
            existing = await db_pool.fetchrow(
                "SELECT id FROM relations WHERE source_id = $1 AND target_id = $2 AND relation_type = $3",
                source_id,
                target_id,
                rel["type"],
            )
            if not existing:
                rid = str(_uuid.uuid4())
                await db_pool.execute(
                    "INSERT INTO relations (id, source_id, target_id, relation_type, properties, user_id) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    rid,
                    source_id,
                    target_id,
                    rel["type"],
                    json.dumps(rel.get("properties", {})),
                    user_id,
                )
                new_relation_count += 1

        return {
            "knowledge_scanned": len(rows),
            "entities_extracted": len(all_entities),
            "relations_extracted": len(all_relations),
            "entities_new": new_entity_count,
            "relations_new": new_relation_count,
            "entity_types": dict(
                defaultdict(
                    int,
                    {
                        e["type"]: sum(1 for v in all_entities.values() if v["type"] == e["type"])
                        for e in all_entities.values()
                    },
                )
            ),
        }

    # ── Query Graph ─────────────────────────────────────────────────

    async def query_graph(
        self, db_pool: Any, user_id: str, entity_name: str, depth: int = 2
    ) -> dict[str, Any]:
        """BFS traversal from an entity, returning the subgraph."""
        entity = await db_pool.fetchrow(
            "SELECT id, name, type, description, properties FROM entities "
            "WHERE name = $1 AND user_id = $2",
            entity_name,
            user_id,
        )
        if not entity:
            return {"error": f"Entity '{entity_name}' not found"}

        visited: set[str] = set()
        result_entities: list[dict] = []
        result_relations: list[dict] = []
        queue: list[tuple[str, int]] = [(str(entity["id"]), 0)]

        while queue:
            current_id, current_depth = queue.pop(0)
            if current_id in visited or current_depth > depth:
                continue
            visited.add(current_id)

            # Get entity info
            ent = await db_pool.fetchrow(
                "SELECT id, name, type, description, properties FROM entities WHERE id = $1",
                current_id,
            )
            if ent:
                props = ent["properties"]
                if isinstance(props, str):
                    try:
                        props = json.loads(props)
                    except (json.JSONDecodeError, TypeError):
                        props = {}
                result_entities.append(
                    {
                        "id": str(ent["id"]),
                        "name": ent["name"],
                        "type": ent["type"],
                        "description": ent["description"] or "",
                        "properties": props or {},
                    }
                )

            # Outgoing relations
            out_rels = await db_pool.fetch(
                "SELECT r.id, r.relation_type, r.properties, te.name AS target_name, te.type AS target_type "
                "FROM relations r JOIN entities te ON r.target_id = te.id "
                "WHERE r.source_id = $1",
                current_id,
            )
            for rel in out_rels:
                result_relations.append(
                    {
                        "id": str(rel["id"]),
                        "source": ent["name"] if ent else "?",
                        "target": rel["target_name"],
                        "type": rel["relation_type"],
                        "direction": "out",
                    }
                )
                target_row = await db_pool.fetchrow(
                    "SELECT id FROM entities WHERE name = $1 AND user_id = $2",
                    rel["target_name"],
                    user_id,
                )
                if target_row:
                    queue.append((str(target_row["id"]), current_depth + 1))

            # Incoming relations
            in_rels = await db_pool.fetch(
                "SELECT r.id, r.relation_type, r.properties, se.name AS source_name, se.type AS source_type "
                "FROM relations r JOIN entities se ON r.source_id = se.id "
                "WHERE r.target_id = $1",
                current_id,
            )
            for rel in in_rels:
                result_relations.append(
                    {
                        "id": str(rel["id"]),
                        "source": rel["source_name"],
                        "target": ent["name"] if ent else "?",
                        "type": rel["relation_type"],
                        "direction": "in",
                    }
                )
                source_row = await db_pool.fetchrow(
                    "SELECT id FROM entities WHERE name = $1 AND user_id = $2",
                    rel["source_name"],
                    user_id,
                )
                if source_row:
                    queue.append((str(source_row["id"]), current_depth + 1))

        return {
            "center": entity_name,
            "entities": result_entities,
            "relations": result_relations,
            "depth": depth,
            "total_entities": len(result_entities),
            "total_relations": len(result_relations),
        }
