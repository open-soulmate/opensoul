"""Long-term memory retrieval and consolidation for OpenHippo.

Fuses the 98-agent architecture research into OpenSoul's hippocampus:
- Four-layer memory: episodic / semantic / procedural / working
- Importance-weighted retrieval with FTS5 full-text search
- Memory consolidation (dedup + merge + importance decay)
- Context injection with token budget control

Borrowed from: Mem0, Zep, MemGPT, LangChain ConversationSummaryMemory
"""

import hashlib
import json
import logging
import math
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("opensoul.hippo.long_term")


@dataclass
class LongTermMemory:
    """A long-term memory entry with four-layer classification."""
    memory_id: str
    content: str
    memory_type: str = "episodic"  # episodic / semantic / procedural / working
    importance: float = 0.5
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    access_count: int = 0
    consolidated: bool = False
    merged_into: str = ""
    source_session: str = ""


def _normalize_dict_floats(
    d: dict[str, float], target_min: float = 0.0, target_max: float = 1.0
) -> dict[str, float]:
    """Normalize float values in a dict to [target_min, target_max].

    Ported from generative-agents retrieve.py normalize_dict_floats().
    If all values are identical (range=0), every value maps to midpoint.
    """
    if not d:
        return d
    min_val = min(d.values())
    max_val = max(d.values())
    range_val = max_val - min_val
    if range_val == 0:
        mid = (target_max - target_min) / 2
        return {k: mid for k in d}
    return {
        k: ((v - min_val) * (target_max - target_min) / range_val + target_min)
        for k, v in d.items()
    }


class LongTermMemoryStore:
    """SQLite-backed long-term memory store with FTS5 search.

    Designed to complement the in-memory MemoryStore:
    - MemoryStore = short-term, session-scoped, decay-based
    - LongTermMemoryStore = persistent, cross-session, retrieval-based
    """

    def __init__(self, db_path: str = ""):
        if not db_path:
            base = Path.home() / ".hermes" / "opensoul" / "hippo"
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "long_term_memory.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    memory_type TEXT DEFAULT 'episodic',
                    importance REAL DEFAULT 0.5,
                    tags TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}',
                    created_at REAL NOT NULL,
                    last_accessed_at REAL NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    consolidated INTEGER DEFAULT 0,
                    merged_into TEXT DEFAULT '',
                    source_session TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mem_type
                ON memories(memory_type, importance)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mem_session
                ON memories(source_session)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mem_consolidated
                ON memories(consolidated, merged_into)
            """)
            # FTS5 full-text search
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(memory_id, content, tags, tokenize='unicode61')
            """)
            conn.commit()

    def store(
        self,
        content: str,
        memory_type: str = "episodic",
        importance: float = 0.5,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
        source_session: str = "",
    ) -> LongTermMemory:
        """Store a new long-term memory."""
        memory_id = f"ltm_{hashlib.sha256(f'{content}:{time.time()}'.encode()).hexdigest()[:12]}"

        mem = LongTermMemory(
            memory_id=memory_id,
            content=content,
            memory_type=memory_type,
            importance=importance,
            tags=tags or [],
            metadata=metadata or {},
            source_session=source_session,
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO memories
                   (memory_id, content, memory_type, importance, tags, metadata,
                    created_at, last_accessed_at, access_count, source_session)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (memory_id, content, memory_type, importance,
                 json.dumps(tags or [], ensure_ascii=False),
                 json.dumps(metadata or {}, ensure_ascii=False),
                 mem.created_at, mem.last_accessed_at, 0, source_session),
            )
            conn.execute(
                "INSERT INTO memories_fts (memory_id, content, tags) VALUES (?, ?, ?)",
                (memory_id, content, json.dumps(tags or [])),
            )
            conn.commit()

        logger.debug(f"Stored LTM: {memory_type} importance={importance:.2f}")
        return mem

    def retrieve(
        self,
        query: str,
        memory_type: str = "",
        min_importance: float = 0.0,
        limit: int = 10,
    ) -> list[dict]:
        """Retrieve memories by relevance (FTS5 + importance weighting)."""
        sql = """
            SELECT m.* FROM memories m
            JOIN memories_fts f ON m.memory_id = f.memory_id
            WHERE memories_fts MATCH ?
              AND m.consolidated = 0 AND m.merged_into = ''
        """
        params: list = [query]

        if memory_type:
            sql += " AND m.memory_type = ?"
            params.append(memory_type)

        sql += " AND m.importance >= ?"
        params.append(min_importance)

        sql += " ORDER BY m.importance DESC, m.access_count DESC LIMIT ?"
        params.append(limit * 3)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Use LIKE directly for Chinese text (FTS5 unicode61 tokenizer is poor for CJK)
            like_sql = """
                SELECT * FROM memories
                WHERE (content LIKE ? OR tags LIKE ?)
                  AND consolidated = 0 AND merged_into = ''
            """
            like_params: list = [f"%{query}%", f"%{query}%"]
            if memory_type:
                like_sql += " AND memory_type = ?"
                like_params.append(memory_type)
            like_sql += " AND importance >= ?"
            like_params.append(min_importance)
            like_sql += " ORDER BY importance DESC, access_count DESC LIMIT ?"
            like_params.append(limit)
            rows = conn.execute(like_sql, like_params).fetchall()

        # Score and rank
        query_lower = query.lower()
        query_tokens = set(re.findall(r'[\w\u4e00-\u9fff]+', query_lower))

        scored = []
        for row in rows:
            d = dict(row)
            d["tags"] = json.loads(d.get("tags", "[]"))
            d["metadata"] = json.loads(d.get("metadata", "{}"))

            content_lower = d["content"].lower()
            content_tokens = set(re.findall(r'[\w\u4e00-\u9fff]+', content_lower))

            # Jaccard similarity
            if query_tokens and content_tokens:
                jaccard = len(query_tokens & content_tokens) / len(query_tokens | content_tokens)
            else:
                jaccard = 0.0

            # Combined score: relevance × importance × access_frequency
            score = (
                jaccard * 0.5 +
                d["importance"] * 0.3 +
                min(1.0, d["access_count"] / 10) * 0.2
            )
            d["relevance_score"] = round(score, 4)
            scored.append(d)

        scored.sort(key=lambda x: x["relevance_score"], reverse=True)
        results = scored[:limit]

        # Update access counts
        if results:
            ids = [r["memory_id"] for r in results]
            with sqlite3.connect(self.db_path) as conn:
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"""UPDATE memories SET
                        access_count = access_count + 1,
                        last_accessed_at = ?
                        WHERE memory_id IN ({placeholders})""",
                    [time.time()] + ids,
                )
                conn.commit()

        return results

    def three_factor_retrieve(
        self,
        query: str,
        memory_type: str = "",
        min_importance: float = 0.0,
        limit: int = 10,
        recency_decay: float = 0.99,
        recency_weight: float = 1.0,
        relevance_weight: float = 1.0,
        importance_weight: float = 1.0,
    ) -> list[dict]:
        """Retrieve memories using three-factor scoring (generative-agents pattern).

        Three factors, each normalized to [0,1] then combined with weights:
        - Recency: exponential decay on hours since last access
          (recency_decay ^ hours_since_access, capped at 1.0)
        - Importance: stored importance value directly
        - Relevance: Jaccard token similarity between query and content

        Reference: gen-agents/reverie/backend_server/persona/cognitive_modules/retrieve.py
        (new_retrieve, lines 199-271) — normalize each factor to [0,1], then
        weighted sum. Paper suggests gw=[1,1,1] as decent default; all three
        factors equal-weighted here.

        The existing retrieve() uses a fixed-weight formula without recency;
        this method adds time-awareness so recently accessed memories surface
        preferentially alongside important and relevant ones.
        """
        # Phase 1: candidate gathering — same LIKE-based search as retrieve()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            like_sql = """
                SELECT * FROM memories
                WHERE (content LIKE ? OR tags LIKE ?)
                  AND consolidated = 0 AND merged_into = ''
            """
            like_params: list = [f"%{query}%", f"%{query}%"]
            if memory_type:
                like_sql += " AND memory_type = ?"
                like_params.append(memory_type)
            like_sql += " AND importance >= ?"
            like_params.append(min_importance)
            like_sql += " ORDER BY last_accessed_at ASC LIMIT ?"
            like_params.append(limit * 3)
            rows = conn.execute(like_sql, like_params).fetchall()

        if not rows:
            return []

        now = time.time()
        query_lower = query.lower()
        query_tokens = set(re.findall(r'[\w\u4e00-\u9fff]+', query_lower))

        # Phase 2: compute three raw factor scores per memory
        recency_raw: dict[str, float] = {}
        importance_raw: dict[str, float] = {}
        relevance_raw: dict[str, float] = {}
        memory_data: dict[str, dict] = {}

        for row in rows:
            d = dict(row)
            d["tags"] = json.loads(d.get("tags", "[]"))
            d["metadata"] = json.loads(d.get("metadata", "{}"))
            mid = d["memory_id"]
            memory_data[mid] = d

            # Recency: 0.99 ^ hours_since_last_access (recent → closer to 1.0)
            idle_hours = max(0.0, (now - d["last_accessed_at"]) / 3600.0)
            recency_raw[mid] = recency_decay ** idle_hours

            # Importance: stored value, already in [0, 1]
            importance_raw[mid] = d["importance"]

            # Relevance: Jaccard token overlap
            content_lower = d["content"].lower()
            content_tokens = set(re.findall(r'[\w\u4e00-\u9fff]+', content_lower))
            if query_tokens and content_tokens:
                relevance_raw[mid] = (
                    len(query_tokens & content_tokens) / len(query_tokens | content_tokens)
                )
            else:
                relevance_raw[mid] = 0.0

        # Phase 3: normalize each factor to [0, 1]
        recency_norm = _normalize_dict_floats(recency_raw)
        importance_norm = _normalize_dict_floats(importance_raw)
        relevance_norm = _normalize_dict_floats(relevance_raw)

        # Phase 4: weighted combination
        scored: list[dict] = []
        for mid, d in memory_data.items():
            score = (
                recency_weight * recency_norm[mid]
                + relevance_weight * relevance_norm[mid]
                + importance_weight * importance_norm[mid]
            ) / (recency_weight + relevance_weight + importance_weight)
            d["three_factor_score"] = round(score, 4)
            d["recency_raw"] = round(recency_raw[mid], 6)
            d["relevance_raw"] = round(relevance_raw[mid], 4)
            scored.append(d)

        scored.sort(key=lambda x: x["three_factor_score"], reverse=True)
        results = scored[:limit]

        # Update access counts for retrieved memories
        if results:
            ids = [r["memory_id"] for r in results]
            with sqlite3.connect(self.db_path) as conn:
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"""UPDATE memories SET
                        access_count = access_count + 1,
                        last_accessed_at = ?
                        WHERE memory_id IN ({placeholders})""",
                    [time.time()] + ids,
                )
                conn.commit()

        return results

    def get_context_prompt(
        self,
        query: str,
        token_budget: int = 500,
        memory_type: str = "",
        use_three_factor: bool = True,
    ) -> str:
        """Generate memory context for injection into system prompt.

        By default uses three_factor_retrieve() (recency+importance+relevance);
        set use_three_factor=False to fall back to the legacy retrieve() scoring.
        """
        if use_three_factor:
            memories = self.three_factor_retrieve(query, memory_type=memory_type, limit=5)
        else:
            memories = self.retrieve(query, memory_type=memory_type, limit=5)
        if not memories:
            return ""

        lines = ["## 相关记忆\n"]
        total_chars = 0
        budget_chars = token_budget * 3
        type_labels = {
            "episodic": "📌 事件",
            "semantic": "📚 知识",
            "procedural": "⚙️ 技能",
            "working": "🔄 工作记忆",
        }

        for mem in memories:
            label = type_labels.get(mem["memory_type"], "📝")
            line = f"- {label} [{mem['importance']:.0%}] {mem['content'][:100]}"
            if len(line) + total_chars > budget_chars:
                break
            lines.append(line)
            total_chars += len(line)

        if len(lines) <= 1:
            return ""

        return "\n".join(lines)

    def consolidate(self) -> dict:
        """Consolidate memories: dedup, merge, decay importance."""
        stats = {"merged": 0, "decayed": 0, "archived": 0}
        now = time.time()

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # 1. Dedup: find similar content (first 100 chars as hash)
            rows = conn.execute(
                """SELECT memory_id, content, importance, access_count
                   FROM memories
                   WHERE consolidated = 0 AND merged_into = ''
                   ORDER BY importance DESC"""
            ).fetchall()

            seen: dict[str, str] = {}
            for row in rows:
                content_hash = hashlib.sha256(row["content"][:100].encode()).hexdigest()
                if content_hash in seen:
                    # Merge into primary
                    primary_id = seen[content_hash]
                    conn.execute(
                        """UPDATE memories SET
                           importance = MAX(importance, ?),
                           access_count = access_count + ?
                           WHERE memory_id = ?""",
                        (row["importance"], row["access_count"], primary_id),
                    )
                    conn.execute(
                        """UPDATE memories SET
                           consolidated = 1, merged_into = ?
                           WHERE memory_id = ?""",
                        (primary_id, row["memory_id"]),
                    )
                    stats["merged"] += 1
                else:
                    seen[content_hash] = row["memory_id"]

            # 2. Importance decay (7 days without access → ×0.9)
            cutoff = now - 7 * 86400
            cursor = conn.execute(
                """UPDATE memories SET importance = importance * 0.9
                   WHERE last_accessed_at < ? AND last_accessed_at > 0
                     AND consolidated = 0""",
                (cutoff,),
            )
            stats["decayed"] = cursor.rowcount or 0

            # 3. Archive low-importance memories
            cursor = conn.execute(
                """UPDATE memories SET consolidated = 1
                   WHERE importance < 0.1 AND access_count < 2
                     AND consolidated = 0""",
            )
            stats["archived"] = cursor.rowcount or 0

            conn.commit()

        logger.info(f"Memory consolidation: {stats}")
        return stats

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE consolidated = 0 AND merged_into = ''"
            ).fetchone()[0]
            by_type = conn.execute(
                """SELECT memory_type, COUNT(*) FROM memories
                   WHERE consolidated = 0 GROUP BY memory_type"""
            ).fetchall()
            avg_importance = conn.execute(
                "SELECT AVG(importance) FROM memories WHERE consolidated = 0"
            ).fetchone()[0]
            total_accesses = conn.execute(
                "SELECT SUM(access_count) FROM memories"
            ).fetchone()[0]

        return {
            "total_memories": total,
            "active_memories": active,
            "by_type": {t[0]: t[1] for t in by_type},
            "avg_importance": round(avg_importance, 3) if avg_importance else 0,
            "total_accesses": total_accesses or 0,
        }
