"""Tests for hippo long-term memory CRUD + mem0 audit trail.

Covers:
- store() writes ADD audit record
- update_memory() with sparse edit + UPDATE audit
- delete_memory() soft/hard delete + DELETE audit
- list_memories() with filters + pagination
- get_history() audit trail queries
- consolidate() writes MERGE audit records
- get_audit_stats() aggregation
- FTS index sync on update/delete
"""
import json
import os
import tempfile
import time
import unittest

from src.hippo.long_term_memory import LongTermMemoryStore, MemoryAuditEntry


class TestMemoryCRUDAndAudit(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test_ltm.db")
        self.store = LongTermMemoryStore(db_path=self._db_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ── store() writes ADD audit ────────────────────────────

    def test_store_writes_add_audit(self):
        mem = self.store.store(
            content="User prefers Python over Rust",
            memory_type="semantic",
            importance=0.8,
            tags=["preference"],
        )
        history = self.store.get_history(memory_id=mem.memory_id)
        assert len(history) == 1
        assert history[0]["event"] == "ADD"
        assert history[0]["reason"] == "store"
        new_val = json.loads(history[0]["new_value"])
        assert "Python" in new_val["content"]

    def test_store_multiple_memories_each_has_audit(self):
        for i in range(3):
            self.store.store(content=f"Memory {i}", memory_type="episodic")
        all_history = self.store.get_history()
        add_events = [h for h in all_history if h["event"] == "ADD"]
        assert len(add_events) == 3

    # ── update_memory() ─────────────────────────────────────

    def test_update_content(self):
        mem = self.store.store(content="Old content", memory_type="semantic")
        result = self.store.update_memory(
            memory_id=mem.memory_id,
            content="New corrected content",
            reason="user_correction",
        )
        assert result is not None
        assert result["content"] == "New corrected content"

    def test_update_importance(self):
        mem = self.store.store(content="Test", importance=0.3)
        result = self.store.update_memory(
            memory_id=mem.memory_id,
            importance=0.9,
        )
        assert result["importance"] == 0.9

    def test_update_tags(self):
        mem = self.store.store(content="Test", tags=["old_tag"])
        result = self.store.update_memory(
            memory_id=mem.memory_id,
            tags=["new_tag", "another"],
        )
        assert result["tags"] == ["new_tag", "another"]

    def test_update_sparse_only_changes_provided_fields(self):
        mem = self.store.store(
            content="Original",
            memory_type="semantic",
            importance=0.6,
            tags=["keep"],
        )
        result = self.store.update_memory(
            memory_id=mem.memory_id,
            importance=0.95,
        )
        assert result["content"] == "Original"  # Unchanged
        assert result["memory_type"] == "semantic"  # Unchanged
        assert result["tags"] == ["keep"]  # Unchanged
        assert result["importance"] == 0.95  # Changed

    def test_update_writes_audit_with_old_new(self):
        mem = self.store.store(content="Before edit", memory_type="semantic")
        self.store.update_memory(
            memory_id=mem.memory_id,
            content="After edit",
            reason="user_correction",
        )
        history = self.store.get_history(memory_id=mem.memory_id, event="UPDATE")
        assert len(history) == 1
        old_val = json.loads(history[0]["old_value"])
        new_val = json.loads(history[0]["new_value"])
        assert old_val["content"] == "Before edit"
        assert new_val["content"] == "After edit"
        assert history[0]["reason"] == "user_correction"

    def test_update_nonexistent_returns_none(self):
        result = self.store.update_memory(memory_id="ltm_nonexistent", content="test")
        assert result is None

    def test_update_no_fields_returns_current(self):
        mem = self.store.store(content="Untouched")
        result = self.store.update_memory(memory_id=mem.memory_id)
        assert result is not None
        assert result["content"] == "Untouched"

    def test_update_syncs_fts_index(self):
        mem = self.store.store(content="searchable_old_term")
        self.store.update_memory(memory_id=mem.memory_id, content="searchable_new_term")
        # Old term should not match via retrieval
        results_old = self.store.retrieve("searchable_old_term")
        assert all(r["memory_id"] != mem.memory_id for r in results_old)
        # New term should match
        results_new = self.store.retrieve("searchable_new_term")
        assert any(r["memory_id"] == mem.memory_id for r in results_new)

    # ── delete_memory() ─────────────────────────────────────

    def test_soft_delete(self):
        mem = self.store.store(content="To be deleted")
        success = self.store.delete_memory(memory_id=mem.memory_id)
        assert success is True
        # Should not appear in list_memories (default excludes deleted)
        listed = self.store.list_memories()
        assert all(m["memory_id"] != mem.memory_id for m in listed)
        # But should appear when include_deleted=True
        listed_del = self.store.list_memories(include_deleted=True)
        assert any(m["memory_id"] == mem.memory_id for m in listed_del)

    def test_hard_delete(self):
        mem = self.store.store(content="Hard delete target")
        success = self.store.delete_memory(memory_id=mem.memory_id, hard_delete=True)
        assert success is True
        # Should not appear even with include_deleted
        listed = self.store.list_memories(include_deleted=True)
        assert all(m["memory_id"] != mem.memory_id for m in listed)

    def test_delete_writes_audit(self):
        mem = self.store.store(content="Delete me", memory_type="episodic")
        self.store.delete_memory(memory_id=mem.memory_id, reason="user_privacy_request")
        history = self.store.get_history(memory_id=mem.memory_id, event="DELETE")
        assert len(history) == 1
        assert history[0]["is_deleted"] == 1
        assert history[0]["reason"] == "user_privacy_request"
        old_val = json.loads(history[0]["old_value"])
        assert "Delete me" in old_val["content"]

    def test_delete_nonexistent_returns_false(self):
        success = self.store.delete_memory(memory_id="ltm_does_not_exist")
        assert success is False

    def test_deleted_memory_not_in_retrieval(self):
        mem = self.store.store(content="retrievable_content_xyz")
        self.store.delete_memory(memory_id=mem.memory_id)
        results = self.store.retrieve("retrievable_content_xyz")
        assert all(r["memory_id"] != mem.memory_id for r in results)

    # ── list_memories() ─────────────────────────────────────

    def test_list_memories_basic(self):
        for i in range(5):
            self.store.store(content=f"Listed memory {i}", memory_type="semantic")
        results = self.store.list_memories()
        assert len(results) == 5

    def test_list_memories_type_filter(self):
        self.store.store(content="Semantic fact", memory_type="semantic")
        self.store.store(content="Episodic event", memory_type="episodic")
        self.store.store(content="Procedural skill", memory_type="procedural")
        semantic = self.store.list_memories(memory_type="semantic")
        assert len(semantic) == 1
        assert semantic[0]["memory_type"] == "semantic"

    def test_list_memories_pagination(self):
        for i in range(10):
            self.store.store(content=f"Paged memory {i}")
        page1 = self.store.list_memories(limit=4, offset=0)
        page2 = self.store.list_memories(limit=4, offset=4)
        page3 = self.store.list_memories(limit=4, offset=8)
        assert len(page1) == 4
        assert len(page2) == 4
        assert len(page3) == 2
        # No overlap
        ids1 = {m["memory_id"] for m in page1}
        ids2 = {m["memory_id"] for m in page2}
        assert not (ids1 & ids2)

    def test_list_memories_excludes_deleted_by_default(self):
        mem1 = self.store.store(content="Alive memory")
        mem2 = self.store.store(content="Dead memory")
        self.store.delete_memory(memory_id=mem2.memory_id)
        results = self.store.list_memories()
        assert len(results) == 1
        assert results[0]["memory_id"] == mem1.memory_id

    def test_list_memories_returns_parsed_json(self):
        self.store.store(content="Test", tags=["tag1", "tag2"], metadata={"key": "val"})
        results = self.store.list_memories()
        assert results[0]["tags"] == ["tag1", "tag2"]
        assert results[0]["metadata"] == {"key": "val"}

    # ── get_history() ───────────────────────────────────────

    def test_get_history_all(self):
        self.store.store(content="Mem A")
        self.store.store(content="Mem B")
        self.store.store(content="Mem C")
        history = self.store.get_history()
        assert len(history) == 3
        assert all(h["event"] == "ADD" for h in history)

    def test_get_history_filter_by_event(self):
        mem = self.store.store(content="Multi-op memory")
        self.store.update_memory(memory_id=mem.memory_id, content="Updated")
        self.store.delete_memory(memory_id=mem.memory_id)
        add_events = self.store.get_history(event="ADD")
        update_events = self.store.get_history(event="UPDATE")
        delete_events = self.store.get_history(event="DELETE")
        assert len(add_events) == 1
        assert len(update_events) == 1
        assert len(delete_events) == 1

    def test_get_history_chronological(self):
        mem = self.store.store(content="Step 1")
        time.sleep(0.01)
        self.store.update_memory(memory_id=mem.memory_id, content="Step 2")
        time.sleep(0.01)
        self.store.update_memory(memory_id=mem.memory_id, content="Step 3")
        history = self.store.get_history(memory_id=mem.memory_id)
        assert len(history) == 3
        # DESC order: most recent first
        assert history[0]["event"] == "UPDATE"
        assert history[-1]["event"] == "ADD"

    def test_get_history_limit(self):
        for i in range(10):
            self.store.store(content=f"Memory {i}")
        history = self.store.get_history(limit=3)
        assert len(history) == 3

    # ── consolidate() writes MERGE audit ────────────────────

    def test_consolidate_writes_merge_audit(self):
        # Store two memories with same first-100-chars content
        self.store.store(content="Duplicate content for merging test", importance=0.8)
        time.sleep(0.01)
        self.store.store(content="Duplicate content for merging test", importance=0.3)
        result = self.store.consolidate()
        assert result["merged"] >= 1
        merge_history = self.store.get_history(event="MERGE")
        assert len(merge_history) >= 1
        new_val = json.loads(merge_history[0]["new_value"])
        assert "merged_into" in new_val

    # ── get_audit_stats() ───────────────────────────────────

    def test_audit_stats(self):
        mem1 = self.store.store(content="Stats memory 1")
        mem2 = self.store.store(content="Stats memory 2")
        self.store.update_memory(memory_id=mem1.memory_id, content="Updated 1")
        self.store.delete_memory(memory_id=mem2.memory_id)
        stats = self.store.get_audit_stats()
        assert stats["total_audit_records"] == 4  # 2 ADD + 1 UPDATE + 1 DELETE
        assert stats["by_event"]["ADD"] == 2
        assert stats["by_event"]["UPDATE"] == 1
        assert stats["by_event"]["DELETE"] == 1
        assert stats["deleted_count"] == 1
        assert len(stats["recent"]) <= 5

    def test_audit_stats_empty(self):
        stats = self.store.get_audit_stats()
        assert stats["total_audit_records"] == 0
        assert stats["by_event"] == {}
        assert stats["deleted_count"] == 0

    # ── MemoryAuditEntry dataclass ──────────────────────────

    def test_memory_audit_entry_dataclass(self):
        entry = MemoryAuditEntry(
            audit_id="aud_test123",
            memory_id="ltm_abc",
            event="UPDATE",
            old_value='{"content": "old"}',
            new_value='{"content": "new"}',
            is_deleted=False,
            reason="user_edit",
        )
        assert entry.audit_id == "aud_test123"
        assert entry.event == "UPDATE"
        assert entry.is_deleted is False
        assert entry.created_at > 0

    # ── Integration: full lifecycle with audit trail ────────

    def test_full_lifecycle_audit_trail(self):
        # ADD
        mem = self.store.store(
            content="User works in Beijing office",
            memory_type="semantic",
            importance=0.7,
            tags=["location", "work"],
        )
        # UPDATE
        self.store.update_memory(
            memory_id=mem.memory_id,
            content="User works in Shanghai office",
            reason="user_relocation",
        )
        # DELETE
        self.store.delete_memory(
            memory_id=mem.memory_id,
            reason="info_outdated",
        )
        # Full audit trail
        history = self.store.get_history(memory_id=mem.memory_id)
        assert len(history) == 3
        events = [h["event"] for h in history]
        # DESC order
        assert events == ["DELETE", "UPDATE", "ADD"]
        # Each record has appropriate data
        delete_record = history[0]
        assert delete_record["is_deleted"] == 1
        assert delete_record["reason"] == "info_outdated"
        update_record = history[1]
        old_data = json.loads(update_record["old_value"])
        new_data = json.loads(update_record["new_value"])
        assert "Beijing" in old_data["content"]
        assert "Shanghai" in new_data["content"]


if __name__ == "__main__":
    unittest.main()
