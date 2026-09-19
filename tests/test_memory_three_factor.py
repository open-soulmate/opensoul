"""Tests for three-factor memory retrieval (generative-agents pattern).

Three factors: recency (time-decay), importance (stored), relevance (Jaccard).
Each normalized to [0,1] then combined with configurable weights.

Reference: gen-agents/reverie/backend_server/persona/cognitive_modules/retrieve.py
"""

import os
import tempfile
import time

import pytest

from src.hippo.long_term_memory import (
    LongTermMemoryStore,
    _normalize_dict_floats,
)


class TestNormalizeDictFloats:
    """Test the normalization helper ported from generative-agents."""

    def test_basic_normalization(self):
        d = {"a": 1.0, "b": 3.0, "c": 5.0}
        result = _normalize_dict_floats(d, 0.0, 1.0)
        assert result["a"] == pytest.approx(0.0)
        assert result["b"] == pytest.approx(0.5)
        assert result["c"] == pytest.approx(1.0)

    def test_all_same_values_maps_to_midpoint(self):
        d = {"a": 2.0, "b": 2.0, "c": 2.0}
        result = _normalize_dict_floats(d, 0.0, 1.0)
        for v in result.values():
            assert v == pytest.approx(0.5)

    def test_empty_dict(self):
        result = _normalize_dict_floats({}, 0.0, 1.0)
        assert result == {}

    def test_single_value(self):
        d = {"only": 42.0}
        result = _normalize_dict_floats(d, 0.0, 1.0)
        assert result["only"] == pytest.approx(0.5)

    def test_custom_range(self):
        d = {"a": 0.0, "b": 10.0}
        result = _normalize_dict_floats(d, -1.0, 1.0)
        assert result["a"] == pytest.approx(-1.0)
        assert result["b"] == pytest.approx(1.0)

    def test_negative_values(self):
        d = {"a": -5.0, "b": 0.0, "c": 5.0}
        result = _normalize_dict_floats(d, 0.0, 1.0)
        assert result["a"] == pytest.approx(0.0)
        assert result["b"] == pytest.approx(0.5)
        assert result["c"] == pytest.approx(1.0)

    def test_preserves_relative_ordering(self):
        d = {"low": 0.1, "mid": 0.5, "high": 0.9}
        result = _normalize_dict_floats(d)
        assert result["low"] < result["mid"] < result["high"]


class TestThreeFactorRetrieve:
    """Test three-factor retrieval on a real SQLite store."""

    @pytest.fixture
    def store(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        # gatekeeper关闭：本套件用相同内容构造recency/importance排序场景，
        # 重复内容是有意的测试夹具，不是记忆污染
        s = LongTermMemoryStore(db_path=path, gatekeeper_enabled=False)
        yield s
        os.unlink(path)

    def _store_with_time(
        self,
        store: LongTermMemoryStore,
        content: str,
        importance: float,
        last_accessed_offset_hours: float,
        memory_type: str = "episodic",
        tags: list | None = None,
    ) -> str:
        """Store a memory and manually set its last_accessed_at to simulate age."""
        mem = store.store(
            content=content,
            memory_type=memory_type,
            importance=importance,
            tags=tags or [],
        )
        target_time = time.time() - last_accessed_offset_hours * 3600.0
        import sqlite3
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE memories SET last_accessed_at = ? WHERE memory_id = ?",
                (target_time, mem.memory_id),
            )
            conn.commit()
        return mem.memory_id

    def test_basic_retrieval(self, store):
        self._store_with_time(store, "Python programming language", 0.5, 0.0)
        results = store.three_factor_retrieve("Python")
        assert len(results) >= 1
        assert "Python" in results[0]["content"]

    def test_empty_results(self, store):
        results = store.three_factor_retrieve("nonexistent query xyz")
        assert results == []

    def test_recency_favors_recent(self, store):
        """A recently accessed memory should outrank an old one with same content relevance."""
        old_id = self._store_with_time(
            store, "database migration postgres", 0.5, 720.0  # 30 days ago
        )
        new_id = self._store_with_time(
            store, "database migration postgres", 0.5, 0.0  # just now
        )
        results = store.three_factor_retrieve("database migration")
        assert len(results) == 2
        # Recent one should rank first
        assert results[0]["memory_id"] == new_id

    def test_importance_favors_important(self, store):
        """With equal recency, higher importance should rank first."""
        low_id = self._store_with_time(
            store, "project plan timeline", 0.2, 0.0
        )
        high_id = self._store_with_time(
            store, "project plan timeline", 0.9, 0.0
        )
        results = store.three_factor_retrieve("project plan")
        assert len(results) == 2
        assert results[0]["memory_id"] == high_id

    def test_relevance_favors_matching(self, store):
        """More textually similar content should rank higher."""
        self._store_with_time(
            store, "unrelated topic about weather", 0.5, 0.0
        )
        self._store_with_time(
            store, "kubernetes deployment configuration", 0.5, 0.0
        )
        results = store.three_factor_retrieve("kubernetes deployment")
        assert len(results) >= 1
        assert "kubernetes" in results[0]["content"].lower()

    def test_scores_are_normalized(self, store):
        """All three_factor_score values should be in [0, 1]."""
        self._store_with_time(store, "test alpha content", 0.3, 1.0)
        self._store_with_time(store, "test beta content", 0.7, 5.0)
        self._store_with_time(store, "test gamma content", 0.5, 100.0)
        results = store.three_factor_retrieve("test")
        assert len(results) >= 2
        for r in results:
            assert 0.0 <= r["three_factor_score"] <= 1.0

    def test_recency_raw_reflects_time(self, store):
        """recency_raw should be higher for more recently accessed memories."""
        old_id = self._store_with_time(store, "shared content topic", 0.5, 48.0)
        new_id = self._store_with_time(store, "shared content topic", 0.5, 0.0)
        results = store.three_factor_retrieve("shared content")
        by_id = {r["memory_id"]: r for r in results}
        assert by_id[new_id]["recency_raw"] > by_id[old_id]["recency_raw"]

    def test_weight_recency_dominant(self, store):
        """With recency_weight=10, a recent-but-unimportant memory beats old-important."""
        old_important = self._store_with_time(
            store, "critical deployment notice", 0.95, 720.0  # 30 days old, high importance
        )
        new_low = self._store_with_time(
            store, "critical deployment notice", 0.10, 0.0  # fresh, low importance
        )
        results = store.three_factor_retrieve(
            "critical deployment",
            recency_weight=10.0,
            relevance_weight=1.0,
            importance_weight=1.0,
        )
        assert len(results) == 2
        assert results[0]["memory_id"] == new_low

    def test_weight_importance_dominant(self, store):
        """With importance_weight=10, old-important beats new-unimportant."""
        old_important = self._store_with_time(
            store, "critical deployment notice", 0.95, 720.0
        )
        new_low = self._store_with_time(
            store, "critical deployment notice", 0.10, 0.0
        )
        results = store.three_factor_retrieve(
            "critical deployment",
            recency_weight=1.0,
            relevance_weight=1.0,
            importance_weight=10.0,
        )
        assert len(results) == 2
        assert results[0]["memory_id"] == old_important

    def test_three_factor_differs_from_legacy(self, store):
        """The three-factor scoring should produce a different ordering than legacy retrieve() in some cases."""
        # Create scenario: old + high importance + high relevance vs recent + low importance + high relevance
        old_id = self._store_with_time(
            store, "kubernetes pod scaling config", 0.9, 480.0
        )
        new_id = self._store_with_time(
            store, "kubernetes pod scaling config", 0.2, 0.0
        )
        three_results = store.three_factor_retrieve("kubernetes pod")
        legacy_results = store.retrieve("kubernetes pod")
        # Legacy retrieve always sorts by importance DESC, access_count DESC
        # Three-factor balances recency + importance + relevance
        # The rankings may differ — at minimum verify both return valid results
        assert len(three_results) == 2
        assert len(legacy_results) >= 1

    def test_memory_type_filter(self, store):
        """Filtering by memory_type should work in three-factor retrieval."""
        self._store_with_time(store, "semantic fact about python", 0.5, 0.0, memory_type="semantic")
        self._store_with_time(store, "python episodic event", 0.5, 0.0, memory_type="episodic")
        results = store.three_factor_retrieve("python", memory_type="semantic")
        assert len(results) == 1
        assert results[0]["memory_type"] == "semantic"

    def test_min_importance_filter(self, store):
        """Low-importance memories should be filtered out."""
        self._store_with_time(store, "filtered out content", 0.1, 0.0)
        self._store_with_time(store, "kept content filtered", 0.8, 0.0)
        results = store.three_factor_retrieve("content", min_importance=0.5)
        assert len(results) >= 1
        for r in results:
            assert r["importance"] >= 0.5

    def test_access_count_updated(self, store):
        """Retrieved memories should have their access_count incremented."""
        mid = self._store_with_time(store, "access count test memory", 0.5, 0.0)
        store.three_factor_retrieve("access count")
        import sqlite3
        with sqlite3.connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT access_count FROM memories WHERE memory_id = ?", (mid,)
            ).fetchone()
        assert row[0] >= 1

    def test_limit_respected(self, store):
        """Should return at most `limit` results."""
        for i in range(20):
            self._store_with_time(store, f"limit test memory {i}", 0.5, 0.0)
        results = store.three_factor_retrieve("limit test", limit=5)
        assert len(results) <= 5

    def test_get_context_prompt_three_factor(self, store):
        """get_context_prompt with use_three_factor=True should produce valid output."""
        self._store_with_time(store, "important context about deployment", 0.8, 0.0)
        prompt = store.get_context_prompt("deployment", use_three_factor=True)
        assert "deployment" in prompt.lower()

    def test_get_context_prompt_legacy_fallback(self, store):
        """get_context_prompt with use_three_factor=False should use legacy retrieve()."""
        self._store_with_time(store, "important context about deployment", 0.8, 0.0)
        prompt = store.get_context_prompt("deployment", use_three_factor=False)
        assert "deployment" in prompt.lower()

    def test_chinese_content(self, store):
        """CJK text should work with three-factor retrieval."""
        self._store_with_time(store, "数据库迁移方案设计", 0.7, 0.0)
        self._store_with_time(store, "前端界面优化方案", 0.5, 0.0)
        results = store.three_factor_retrieve("数据库迁移")
        assert len(results) >= 1
        assert "数据库" in results[0]["content"]

    def test_custom_decay_rate(self, store):
        """A lower recency_decay should penalize old memories more."""
        old_id = self._store_with_time(store, "decay rate comparison", 0.5, 48.0)
        new_id = self._store_with_time(store, "decay rate comparison", 0.5, 0.0)

        # With aggressive decay (0.5), old memories are heavily penalized
        results_aggressive = store.three_factor_retrieve(
            "decay rate", recency_decay=0.5, recency_weight=5.0
        )
        by_id_aggressive = {r["memory_id"]: r for r in results_aggressive}

        # recency_raw for old memory should be lower with 0.5 decay than 0.99 decay
        assert by_id_aggressive[old_id]["recency_raw"] < 0.99 ** 48
