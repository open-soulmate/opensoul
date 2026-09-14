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
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                # FTS query failed, fallback to LIKE
                rows = conn.execute(
                    """SELECT * FROM memories
                       WHERE (content LIKE ? OR tags LIKE ?)
                         AND consolidated = 0 AND merged_into = ''
                       ORDER BY importance DESC, access_count DESC LIMIT ?""",
                    (f"%{query}%", f"%{query}%", limit),
                ).fetchall()

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

    def get_context_prompt(
        self,
        query: str,
        token_budget: int = 500,
        memory_type: str = "",
    ) -> str:
        """Generate memory context for injection into system prompt."""
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
