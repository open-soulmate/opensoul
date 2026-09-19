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

from src.hippo.extractors.deermem_tags import (
    TagDecision,
    infer_tags,
    validate_delete_tags,
    validate_write_tags,
)
from src.hippo.extractors.gatekeeper import GateDecision, MemoryGatekeeper

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


@dataclass
class MemoryAuditEntry:
    """Audit trail entry for memory operations (mem0 pattern).

    Every store/update/delete writes an audit record so that
    "this memory was changed by whom, because of what, from what to what"
    is fully traceable. Schema mirrors mem0's history table:
    memory_id / old / new / event / is_deleted.
    """
    audit_id: str
    memory_id: str
    event: str  # ADD / UPDATE / DELETE / MERGE / DECAY
    old_value: str = ""  # JSON snapshot before change (empty for ADD)
    new_value: str = ""  # JSON snapshot after change (empty for DELETE)
    is_deleted: bool = False
    reason: str = ""  # human-readable: "user_edit", "consolidation_merge", etc.
    created_at: float = field(default_factory=time.time)


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

    def __init__(
        self,
        db_path: str = "",
        gatekeeper: Optional[MemoryGatekeeper] = None,
        gatekeeper_enabled: bool = True,
    ):
        if not db_path:
            base = Path.home() / ".hermes" / "opensoul" / "hippo"
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "long_term_memory.db")
        self.db_path = db_path
        # LobeChat gatekeeper：记忆准入判定，默认开启（gatekeeper_enabled=False仅测试用）
        self.gatekeeper = (
            gatekeeper
            if gatekeeper is not None
            else MemoryGatekeeper(enabled=gatekeeper_enabled)
        )
        # DeerMem标签门可观测状态（每次store/delete更新，API层读取）
        self.last_write_outcome: str = ""  # added / merged / rejected_tags / rejected_gate
        self.last_tag_decision: Optional[TagDecision] = None
        self.last_delete_decision: Optional[TagDecision] = None
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
            # Audit trail table (mem0 pattern: memory_id / old / new / event / is_deleted)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_audit (
                    audit_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    old_value TEXT DEFAULT '',
                    new_value TEXT DEFAULT '',
                    is_deleted INTEGER DEFAULT 0,
                    reason TEXT DEFAULT '',
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_memory
                ON memory_audit(memory_id, created_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_event
                ON memory_audit(event, created_at)
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
        force: bool = False,
        safety_tags: Optional[dict] = None,
        write_mode: str = "auto",
        dup_policy: str = "reject",
    ) -> Optional[LongTermMemory]:
        """Store a new long-term memory.

        P1 gatekeeper（LobeChat记忆守门员）：写入前过准入判定。
        - reject：不入库，写GATE_REJECT审计（mem0 §1.1失败必须可见），返回None
        - force=True：旁路全部准入规则（显式人工覆盖）

        DeerMem写侧安全（18-deer-flow-source.md #10/#11）：
        - safety_tags/write_mode：抽取提议必须带scope/durability/authority三标签，
          自动写(write_mode="auto")只接受user+durable+descriptive；缺失标签由
          确定性推断补齐后照常校验，不合规→TAG_REJECT审计+返回None（fail-closed可见）
        - dup_policy="merge"：近重复fact并入既有记忆（保留原id、importance取max、
          MERGE审计），而非gatekeeper默认的reject——fact_dedup门仅并入同类别
          (memory_type相同)fact，跨类别近重复维持reject
        - resolved标签写入metadata["deermem_tags"]（删除门的依据）
        """
        # ── 准入判定（gatekeeper在真实写入路径上，覆盖ltm_add/dream/import三个入口）──
        if self.gatekeeper.enabled and not force:
            decision = self.gatekeeper.evaluate(
                content,
                memory_type=memory_type,
                recent_contents=self._recent_memory_contents(),
                force=False,
            )
            if not decision.admitted:
                # DeerMem #10 fact_dedup：近重复→同类别并入而非追加
                if (
                    dup_policy == "merge"
                    and decision.rule in ("duplicate_exact", "duplicate_near")
                    and decision.duplicate_of
                ):
                    tag_dec = (
                        TagDecision(
                            accepted=True,
                            rule="bypass",
                            reason="force",
                            tags=infer_tags(content, memory_type),
                        )
                        if force
                        else validate_write_tags(
                            safety_tags,
                            content=content,
                            memory_type=memory_type,
                            write_mode=write_mode,
                        )
                    )
                    if not tag_dec.accepted:
                        self._reject_on_tags(
                            tag_dec, content, memory_type, gate_decision=decision
                        )
                        return None
                    merged = self._merge_into_existing(
                        duplicate_of=decision.duplicate_of,
                        content=content,
                        memory_type=memory_type,
                        importance=importance,
                        tag_dec=tag_dec,
                        similarity=decision.similarity,
                        dup_rule=decision.rule,
                        source_session=source_session,
                    )
                    if merged is not None:
                        return merged
                    # 目标缺失/跨类别：落到下方GATE_REJECT审计（原因标注）
                # 拒绝也必须可见：落审计而非静默丢弃
                extra_reason = ""
                if (
                    dup_policy == "merge"
                    and decision.rule in ("duplicate_exact", "duplicate_near")
                ):
                    extra_reason = " (cross_category_not_mergeable)"
                self._write_audit(
                    memory_id=f"gate_{hashlib.sha256(f'{content}:{time.time()}'.encode()).hexdigest()[:12]}",
                    event="GATE_REJECT",
                    old_value="",
                    new_value=json.dumps(
                        {
                            "content": (content or "")[:200],
                            "memory_type": memory_type,
                            "rule": decision.rule,
                            "reason": decision.reason + extra_reason,
                            "duplicate_of": decision.duplicate_of,
                        },
                        ensure_ascii=False,
                    ),
                    reason=f"gatekeeper:{decision.rule}",
                )
                self.last_write_outcome = "rejected_gate"
                self.last_tag_decision = None
                return None
        else:
            self.gatekeeper.evaluate(content, force=True)

        # ── DeerMem #11 写侧安全标签门（gatekeeper准入之后、入库之前）──
        if force:
            tag_dec = TagDecision(
                accepted=True,
                rule="bypass",
                reason="force",
                tags=infer_tags(content, memory_type),
            )
        else:
            tag_dec = validate_write_tags(
                safety_tags,
                content=content,
                memory_type=memory_type,
                write_mode=write_mode,
            )
            if not tag_dec.accepted:
                self._reject_on_tags(tag_dec, content, memory_type)
                return None
        self.last_tag_decision = tag_dec

        memory_id = f"ltm_{hashlib.sha256(f'{content}:{time.time()}'.encode()).hexdigest()[:12]}"

        # resolved安全标签随metadata落库（删除门/审计的依据）
        resolved_metadata = dict(metadata or {})
        resolved_metadata["deermem_tags"] = (
            tag_dec.tags.to_dict() if tag_dec.tags else {}
        )

        mem = LongTermMemory(
            memory_id=memory_id,
            content=content,
            memory_type=memory_type,
            importance=importance,
            tags=tags or [],
            metadata=resolved_metadata,
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
                 json.dumps(resolved_metadata, ensure_ascii=False),
                 mem.created_at, mem.last_accessed_at, 0, source_session),
            )
            conn.execute(
                'INSERT INTO memories_fts (memory_id, content, tags) VALUES (?, ?, ?)',
                (memory_id, content, json.dumps(tags or [])),
            )
            conn.commit()

        logger.debug(f"Stored LTM: {memory_type} importance={importance:.2f}")
        # Write ADD audit record (mem0 pattern)
        self._write_audit(
            memory_id=memory_id,
            event="ADD",
            old_value="",
            new_value=json.dumps({
                "content": content[:200],
                "memory_type": memory_type,
                "importance": importance,
                "deermem_tags": resolved_metadata["deermem_tags"],
            }, ensure_ascii=False),
            reason="store",
        )
        self.last_write_outcome = "added"
        return mem

    def _reject_on_tags(
        self,
        tag_dec: TagDecision,
        content: str,
        memory_type: str,
        gate_decision: Optional[GateDecision] = None,
    ):
        """TAG_REJECT审计（mem0 §1.1：标签门拒绝必须可见，绝不静默）。"""
        self._write_audit(
            memory_id=f"tag_{hashlib.sha256(f'{content}:{time.time()}'.encode()).hexdigest()[:12]}",
            event="TAG_REJECT",
            old_value="",
            new_value=json.dumps(
                {
                    "content": (content or "")[:200],
                    "memory_type": memory_type,
                    "rule": tag_dec.rule,
                    "reason": tag_dec.reason,
                    "tags": tag_dec.tags.to_dict() if tag_dec.tags else None,
                    "duplicate_of": gate_decision.duplicate_of if gate_decision else "",
                },
                ensure_ascii=False,
            ),
            reason=f"deermem_tags:{tag_dec.rule}",
        )
        self.last_write_outcome = "rejected_tags"
        self.last_tag_decision = tag_dec
        logger.info("DeerMem tag gate reject rule=%s", tag_dec.rule)

    def _merge_into_existing(
        self,
        duplicate_of: str,
        content: str,
        memory_type: str,
        importance: float,
        tag_dec: TagDecision,
        similarity: float,
        dup_rule: str,
        source_session: str = "",
    ) -> Optional[LongTermMemory]:
        """DeerMem #10 fact_dedup：近重复并入既有fact。

        - 仅同类别（memory_type相同）并入；跨类别返回None（调用方回退reject）
        - 保留原memory_id与原content；importance取max（"confidence取max"）
        - 被并入内容记入metadata["deermem_merges"]（有界，最新20条）
        - 写MERGE审计（reason=fact_dedup:<rule>），全程可追溯
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT * FROM memories
                   WHERE memory_id = ? AND consolidated = 0 AND merged_into = ''""",
                (duplicate_of,),
            ).fetchone()
        if row is None:
            return None
        if row["memory_type"] != memory_type:
            return None  # 跨类别近重复不并入（DeerMem：同类别fact才merge）

        old_importance = float(row["importance"])
        new_importance = max(old_importance, importance)
        old_metadata = json.loads(row["metadata"] or "{}")
        merge_log = old_metadata.get("deermem_merges", [])
        merge_log.append(
            {
                "content": (content or "")[:200],
                "importance": importance,
                "similarity": round(similarity, 3),
                "rule": dup_rule,
                "at": time.time(),
            }
        )
        old_metadata["deermem_merges"] = merge_log[-20:]
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE memories
                   SET importance = ?, metadata = ?, last_accessed_at = ?
                   WHERE memory_id = ?""",
                (
                    new_importance,
                    json.dumps(old_metadata, ensure_ascii=False),
                    now,
                    duplicate_of,
                ),
            )
            conn.commit()

        self._write_audit(
            memory_id=duplicate_of,
            event="MERGE",
            old_value=json.dumps(
                {
                    "content": row["content"][:200],
                    "importance": old_importance,
                },
                ensure_ascii=False,
            ),
            new_value=json.dumps(
                {
                    "merged_content": (content or "")[:200],
                    "importance": new_importance,
                    "similarity": round(similarity, 3),
                    "rule": dup_rule,
                },
                ensure_ascii=False,
            ),
            reason=f"fact_dedup:{dup_rule}",
        )
        self.last_write_outcome = "merged"
        self.last_tag_decision = tag_dec
        logger.info(
            "DeerMem fact_dedup merged into %s (similarity=%.2f, importance %.2f->%.2f)",
            duplicate_of,
            similarity,
            old_importance,
            new_importance,
        )
        return LongTermMemory(
            memory_id=duplicate_of,
            content=row["content"],
            memory_type=row["memory_type"],
            importance=new_importance,
            tags=json.loads(row["tags"] or "[]"),
            metadata=old_metadata,
            created_at=row["created_at"],
            last_accessed_at=now,
            access_count=row["access_count"],
            consolidated=bool(row["consolidated"]),
            merged_into=row["merged_into"],
            source_session=row["source_session"] or source_session,
        )

    def _write_audit(
        self,
        memory_id: str,
        event: str,
        old_value: str = "",
        new_value: str = "",
        reason: str = "",
        is_deleted: bool = False,
    ):
        """Write an audit trail record (mem0 history pattern)."""
        audit_id = f"aud_{hashlib.sha256(f'{memory_id}:{event}:{time.time()}'.encode()).hexdigest()[:12]}"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO memory_audit
                   (audit_id, memory_id, event, old_value, new_value, is_deleted, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (audit_id, memory_id, event, old_value, new_value,
                 1 if is_deleted else 0, reason, time.time()),
            )
            conn.commit()

    def update_memory(
        self,
        memory_id: str,
        content: Optional[str] = None,
        memory_type: Optional[str] = None,
        importance: Optional[float] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
        reason: str = "user_edit",
    ) -> Optional[dict]:
        """Update a long-term memory with full audit trail (Khoj CRUD + mem0 audit).

        Only updates fields that are explicitly provided (sparse edit pattern).
        Writes an UPDATE audit record with old/new snapshots.
        Returns the updated memory dict, or None if not found.
        """
        # Fetch current state
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        if not row:
            return None

        old_snapshot = {
            "content": row["content"],
            "memory_type": row["memory_type"],
            "importance": row["importance"],
            "tags": row["tags"],
        }

        # Build update SQL for provided fields only
        updates = []
        params = []
        new_snapshot = dict(old_snapshot)

        if content is not None:
            updates.append("content = ?")
            params.append(content)
            new_snapshot["content"] = content
        if memory_type is not None:
            updates.append("memory_type = ?")
            params.append(memory_type)
            new_snapshot["memory_type"] = memory_type
        if importance is not None:
            updates.append("importance = ?")
            params.append(importance)
            new_snapshot["importance"] = importance
        if tags is not None:
            tags_json = json.dumps(tags, ensure_ascii=False)
            updates.append("tags = ?")
            params.append(tags_json)
            new_snapshot["tags"] = tags_json
        if metadata is not None:
            updates.append("metadata = ?")
            params.append(json.dumps(metadata, ensure_ascii=False))

        if not updates:
            return dict(row)  # Nothing to update

        with sqlite3.connect(self.db_path) as conn:
            # Update main table
            sql = f"UPDATE memories SET {', '.join(updates)} WHERE memory_id = ?"
            params.append(memory_id)
            conn.execute(sql, params)

            # Update FTS index if content or tags changed
            if content is not None or tags is not None:
                conn.execute(
                    "DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,)
                )
                new_content = content if content is not None else row["content"]
                new_tags = tags if tags is not None else json.loads(row["tags"])
                conn.execute(
                    "INSERT INTO memories_fts (memory_id, content, tags) VALUES (?, ?, ?)",
                    (memory_id, new_content, json.dumps(new_tags)),
                )

            # Fetch updated row
            conn.row_factory = sqlite3.Row
            updated = conn.execute(
                "SELECT * FROM memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
            conn.commit()

        # Write UPDATE audit record
        self._write_audit(
            memory_id=memory_id,
            event="UPDATE",
            old_value=json.dumps(old_snapshot, ensure_ascii=False),
            new_value=json.dumps(new_snapshot, ensure_ascii=False),
            reason=reason,
        )

        result = dict(updated)
        result["tags"] = json.loads(result.get("tags", "[]"))
        result["metadata"] = json.loads(result.get("metadata", "{}"))
        return result

    def delete_memory(
        self,
        memory_id: str,
        reason: str = "user_delete",
        hard_delete: bool = False,
        delete_mode: str = "explicit",
        replacement: str = "",
    ) -> bool:
        """Delete a long-term memory with audit trail.

        Soft delete (default): marks consolidated=1 so it stops appearing in
        retrieval but data remains for audit. Hard delete: removes the row
        entirely (audit trail preserved separately).

        DeerMem #11 删除安全门：
        - 矛盾事实（authority=contradiction）删除必须带reason+replacement（两种模式强制）
        - task/project域事实在自动路径(delete_mode="auto"，如dream)fail-closed
        - 人工CRUD路径(delete_mode="explicit")可删task/project域（人的权威）
        - 拦截→DELETE_BLOCKED审计+返回False（mem0 §1.1失败可见）
        """
        # Check existence + fetch snapshot for audit
        self.last_delete_decision = None  # 防止上次判定残留误导调用方
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        if not row:
            return False

        snapshot = {
            "content": row["content"],
            "memory_type": row["memory_type"],
            "importance": row["importance"],
        }

        # ── DeerMem #11 删除安全门（在任何删除动作之前）──
        meta = json.loads(row["metadata"] or "{}")
        stored_tags = meta.get("deermem_tags") or None
        dec = validate_delete_tags(
            stored_tags,
            content=row["content"],
            reason=reason,
            replacement=replacement,
            mode=delete_mode,
        )
        self.last_delete_decision = dec
        if not dec.accepted:
            self._write_audit(
                memory_id=memory_id,
                event="DELETE_BLOCKED",
                old_value=json.dumps(snapshot, ensure_ascii=False),
                new_value=json.dumps(
                    {
                        "rule": dec.rule,
                        "reason": dec.reason,
                        "tags": dec.tags.to_dict() if dec.tags else None,
                        "delete_mode": delete_mode,
                    },
                    ensure_ascii=False,
                ),
                reason=f"deermem_tags:{dec.rule}",
                is_deleted=False,
            )
            logger.info(
                "DeerMem delete gate blocked %s rule=%s", memory_id, dec.rule
            )
            return False

        with sqlite3.connect(self.db_path) as conn:
            if hard_delete:
                conn.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
                conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,))
            else:
                # Soft delete: mark as consolidated so retrieval skips it
                conn.execute(
                    "UPDATE memories SET consolidated = 1 WHERE memory_id = ?",
                    (memory_id,),
                )
                conn.execute(
                    "DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,)
                )
            conn.commit()

        # Write DELETE audit record
        self._write_audit(
            memory_id=memory_id,
            event="DELETE",
            old_value=json.dumps(snapshot, ensure_ascii=False),
            new_value="",
            reason=reason,
            is_deleted=True,
        )
        return True

    def list_memories(
        self,
        memory_type: str = "",
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List all long-term memories (Khoj CRUD: user can see what AI remembers).

        Args:
            memory_type: Filter by type (episodic/semantic/procedural/working).
            include_deleted: If True, also show soft-deleted (consolidated) memories.
            limit: Max results.
            offset: Pagination offset.
        """
        sql = "SELECT * FROM memories WHERE 1=1"
        params: list = []
        if memory_type:
            sql += " AND memory_type = ?"
            params.append(memory_type)
        if not include_deleted:
            sql += " AND consolidated = 0 AND merged_into = ''"
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            d["tags"] = json.loads(d.get("tags", "[]"))
            d["metadata"] = json.loads(d.get("metadata", "{}"))
            d["is_deleted"] = bool(d.get("consolidated", 0))
            results.append(d)
        return results

    def get_history(
        self,
        memory_id: str = "",
        event: str = "",
        limit: int = 50,
    ) -> list[dict]:
        """Get audit trail history (mem0 pattern).

        Args:
            memory_id: Filter by specific memory. Empty = all memories.
            event: Filter by event type (ADD/UPDATE/DELETE/MERGE/DECAY).
            limit: Max results.
        """
        sql = "SELECT * FROM memory_audit WHERE 1=1"
        params: list = []
        if memory_id:
            sql += " AND memory_id = ?"
            params.append(memory_id)
        if event:
            sql += " AND event = ?"
            params.append(event)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()

        return [dict(r) for r in rows]

    def get_audit_stats(self) -> dict:
        """Get audit trail statistics."""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM memory_audit").fetchone()[0]
            by_event = conn.execute(
                "SELECT event, COUNT(*) FROM memory_audit GROUP BY event"
            ).fetchall()
            deleted = conn.execute(
                "SELECT COUNT(*) FROM memory_audit WHERE is_deleted = 1"
            ).fetchone()[0]
            recent = conn.execute(
                """SELECT memory_id, event, reason, created_at
                   FROM memory_audit ORDER BY created_at DESC LIMIT 5"""
            ).fetchall()

        return {
            "total_audit_records": total,
            "by_event": {e[0]: e[1] for e in by_event},
            "deleted_count": deleted,
            "recent": [
                {"memory_id": r[0], "event": r[1], "reason": r[2], "created_at": r[3]}
                for r in recent
            ],
        }

    def _recent_memory_contents(self, limit: int = 300) -> list[tuple[str, str]]:
        """最近活跃记忆(memory_id, content)候选集 — gatekeeper重复判定的输入。

        有界扫描（默认最近300条）：写入量级下O(limit)可接受；
        大库场景可换FTS预筛（遗留优化项）。
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT memory_id, content FROM memories
                   WHERE consolidated = 0 AND merged_into = ''
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def get_gatekeeper_stats(self) -> dict:
        """gatekeeper准入统计 — 进程内计数 + 跨进程审计历史（memory_audit GATE_REJECT）。

        用户极度重视可观测性：拒了什么、为什么拒，必须能查。
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT reason, COUNT(*) FROM memory_audit
                   WHERE event = 'GATE_REJECT' GROUP BY reason"""
            ).fetchall()
            total_rejected = conn.execute(
                "SELECT COUNT(*) FROM memory_audit WHERE event = 'GATE_REJECT'"
            ).fetchone()[0]
            recent_rows = conn.execute(
                """SELECT memory_id, reason, new_value, created_at
                   FROM memory_audit WHERE event = 'GATE_REJECT'
                   ORDER BY created_at DESC LIMIT 10"""
            ).fetchall()
        stats = self.gatekeeper.stats()
        stats["audit"] = {
            "total_rejected_records": total_rejected,
            "by_reason": {r[0]: r[1] for r in rows},
            "recent_rejections": [
                {
                    "gate_id": r[0],
                    "reason": r[1],
                    "detail": r[2],
                    "created_at": r[3],
                }
                for r in recent_rows
            ],
        }
        # DeerMem可观测性：标签门/删除门/fact_dedup计数（用户重视"能看到在干嘛"）
        with sqlite3.connect(self.db_path) as conn:
            tag_rejected = conn.execute(
                "SELECT COUNT(*) FROM memory_audit WHERE event = 'TAG_REJECT'"
            ).fetchone()[0]
            delete_blocked = conn.execute(
                "SELECT COUNT(*) FROM memory_audit WHERE event = 'DELETE_BLOCKED'"
            ).fetchone()[0]
            fact_dedup_merged = conn.execute(
                """SELECT COUNT(*) FROM memory_audit
                   WHERE event = 'MERGE' AND reason LIKE 'fact_dedup:%'"""
            ).fetchone()[0]
            recent_dm_rows = conn.execute(
                """SELECT memory_id, event, reason, new_value, created_at
                   FROM memory_audit
                   WHERE event IN ('TAG_REJECT', 'DELETE_BLOCKED')
                   ORDER BY created_at DESC LIMIT 10"""
            ).fetchall()
        stats["deermem"] = {
            "tag_rejected": tag_rejected,
            "delete_blocked": delete_blocked,
            "fact_dedup_merged": fact_dedup_merged,
            "last_write_outcome": self.last_write_outcome,
            "recent_blocks": [
                {
                    "memory_id": r[0],
                    "event": r[1],
                    "reason": r[2],
                    "detail": r[3],
                    "created_at": r[4],
                }
                for r in recent_dm_rows
            ],
        }
        return stats

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
            merge_audit_records: list[tuple[str, str, float, float]] = []
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
                    merge_audit_records.append((
                        row["memory_id"], primary_id,
                        row["importance"], row["access_count"],
                    ))
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

        # Write MERGE audit records (mem0 pattern)
        for merged_id, primary_id, old_importance, old_access in merge_audit_records:
            self._write_audit(
                memory_id=merged_id,
                event="MERGE",
                old_value=json.dumps({
                    "importance": old_importance,
                    "access_count": old_access,
                }),
                new_value=json.dumps({"merged_into": primary_id}),
                reason="consolidation_dedup",
            )

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
