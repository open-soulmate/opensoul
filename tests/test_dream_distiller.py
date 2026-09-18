"""Tests for hippo Dream distillation + memory echo blocker.

Covers:
- Memory echo blocker (kilocode pattern)
- Dream action parsing (CowAgent 5-step output)
- Dream distillation pipeline (mock LLM)
- Dream stats
- Anti-hallucination prompt content
"""
import asyncio
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.hippo.dream_distiller import (
    DreamAction,
    DreamDistiller,
    DreamResult,
    DREAM_SYSTEM_PROMPT,
    _parse_dream_actions,
)
from src.hippo.long_term_memory import LongTermMemoryStore


def _make_store():
    """Create an isolated LTM store for testing."""
    tmp = tempfile.mktemp(suffix=".db")
    return LongTermMemoryStore(db_path=tmp)


def _make_distiller(store=None, llm_response=None):
    """Create a DreamDistiller with a mock LLM."""
    if store is None:
        store = _make_store()

    async def mock_llm(system_prompt, user_prompt):
        return llm_response or "[]"

    return DreamDistiller(ltm_store=store, llm_call=mock_llm), store


# ── Memory Echo Blocker Tests (kilocode pattern) ──────────────


class TestMemoryEchoBlocker:
    """kilocode recalledMemory(): recall命中过的回合跳过digest防自我污染"""

    def test_no_recall_allows_digest(self):
        distiller, _ = _make_distiller()
        assert not distiller.should_skip_digest()

    def test_recall_blocks_digest(self):
        distiller, _ = _make_distiller()
        distiller.mark_recall(["ltm_abc", "ltm_def"])
        assert distiller.should_skip_digest()

    def test_echo_stats_reflect_recall(self):
        distiller, _ = _make_distiller()
        distiller.mark_recall(["ltm_1", "ltm_2", "ltm_3"])
        stats = distiller.echo_stats
        assert stats["recalled_this_turn"] == 3
        assert stats["unique_recalled"] == 3
        assert stats["digest_blocked"] is True

    def test_reset_turn_clears_echo(self):
        distiller, _ = _make_distiller()
        distiller.mark_recall(["ltm_abc"])
        assert distiller.should_skip_digest()
        distiller.reset_turn()
        assert not distiller.should_skip_digest()

    def test_dream_blocked_by_echo(self):
        """Dream with echo blocker active returns echo_blocked=True."""
        response = json.dumps([{"action": "ADD", "content": "test", "reason": "test"}])
        distiller, store = _make_distiller(llm_response=response)
        distiller.mark_recall(["ltm_xyz"])

        messages = [{"role": "user", "content": "hello"}]
        result = asyncio.run(distiller.dream(messages=messages))
        assert result.echo_blocked is True
        assert "echo blocker" in result.error.lower() or "recalled" in result.error.lower()
        assert result.applied == 0  # Nothing was applied

    def test_dream_force_bypasses_echo(self):
        """force=True bypasses the echo blocker for manual triggers."""
        response = json.dumps([
            {"action": "ADD", "content": "test memory", "memory_type": "semantic",
             "importance": 0.7, "reason": "new info"}
        ])
        distiller, store = _make_distiller(llm_response=response)
        distiller.mark_recall(["ltm_xyz"])

        messages = [{"role": "user", "content": "hello"}]
        result = asyncio.run(distiller.dream(messages=messages, force=True))
        assert result.echo_blocked is False
        assert result.applied == 1


# ── Dream Action Parsing Tests ────────────────────────────────


class TestDreamActionParsing:
    """Parse CowAgent 5-step distillation output into DreamAction list."""

    def test_parse_add_action(self):
        response = json.dumps([
            {"action": "ADD", "content": "User prefers Python", "memory_type": "semantic",
             "importance": 0.8, "tags": ["preference"], "reason": "stated in conversation"}
        ])
        actions = _parse_dream_actions(response)
        assert len(actions) == 1
        assert actions[0].action == "ADD"
        assert actions[0].content == "User prefers Python"
        assert actions[0].importance == 0.8
        assert actions[0].memory_type == "semantic"

    def test_parse_update_action(self):
        response = json.dumps([
            {"action": "UPDATE", "memory_id": "ltm_abc", "new_content": "Updated info",
             "reason": "conflict resolution"}
        ])
        actions = _parse_dream_actions(response)
        assert len(actions) == 1
        assert actions[0].action == "UPDATE"
        assert actions[0].memory_id == "ltm_abc"
        assert actions[0].new_content == "Updated info"

    def test_parse_delete_action(self):
        response = json.dumps([
            {"action": "DELETE", "memory_id": "ltm_xyz", "reason": "outdated info"}
        ])
        actions = _parse_dream_actions(response)
        assert len(actions) == 1
        assert actions[0].action == "DELETE"
        assert actions[0].memory_id == "ltm_xyz"

    def test_parse_skip_action(self):
        response = json.dumps([
            {"action": "SKIP", "memory_id": "ltm_keep", "reason": "still valid"}
        ])
        actions = _parse_dream_actions(response)
        assert len(actions) == 1
        assert actions[0].action == "SKIP"

    def test_parse_fenced_json(self):
        """Handle ```json fenced code blocks."""
        response = '```json\n[{"action": "ADD", "content": "test", "reason": "x"}]\n```'
        actions = _parse_dream_actions(response)
        assert len(actions) == 1
        assert actions[0].action == "ADD"

    def test_parse_bare_json_in_text(self):
        """Handle JSON array embedded in prose."""
        response = 'Here are the operations:\n[{"action": "ADD", "content": "info", "reason": "y"}]\nDone.'
        actions = _parse_dream_actions(response)
        assert len(actions) == 1

    def test_parse_rejects_invalid_actions(self):
        """Non-standard action types are filtered out."""
        response = json.dumps([
            {"action": "INVALID", "content": "x"},
            {"action": "ADD", "content": "valid", "reason": "ok"},
        ])
        actions = _parse_dream_actions(response)
        assert len(actions) == 1
        assert actions[0].content == "valid"

    def test_parse_add_requires_content(self):
        """ADD without content is rejected."""
        response = json.dumps([
            {"action": "ADD", "content": "", "reason": "empty"},
            {"action": "ADD", "content": "has content", "reason": "ok"},
        ])
        actions = _parse_dream_actions(response)
        assert len(actions) == 1
        assert actions[0].content == "has content"

    def test_parse_update_requires_memory_id(self):
        """UPDATE without memory_id is rejected."""
        response = json.dumps([
            {"action": "UPDATE", "new_content": "x", "reason": "no id"},
            {"action": "UPDATE", "memory_id": "ltm_1", "new_content": "y", "reason": "ok"},
        ])
        actions = _parse_dream_actions(response)
        assert len(actions) == 1
        assert actions[0].memory_id == "ltm_1"

    def test_parse_importance_clamped(self):
        """Importance values are clamped to [0.0, 1.0]."""
        response = json.dumps([
            {"action": "ADD", "content": "high", "importance": 5.0, "reason": "x"},
            {"action": "ADD", "content": "low", "importance": -1.0, "reason": "y"},
        ])
        actions = _parse_dream_actions(response)
        assert len(actions) == 2
        assert all(0.0 <= a.importance <= 1.0 for a in actions)

    def test_parse_invalid_memory_type_defaults(self):
        """Invalid memory_type falls back to 'semantic'."""
        response = json.dumps([
            {"action": "ADD", "content": "x", "memory_type": "invalid_type", "reason": "z"},
        ])
        actions = _parse_dream_actions(response)
        assert len(actions) == 1
        assert actions[0].memory_type == "semantic"

    def test_parse_empty_response(self):
        """Empty or non-JSON response returns empty list."""
        assert _parse_dream_actions("") == []
        assert _parse_dream_actions("No memories to process.") == []

    def test_parse_json_object_not_array(self):
        """JSON object (not array) returns empty list."""
        response = '{"action": "ADD", "content": "x"}'
        assert _parse_dream_actions(response) == []


# ── Dream Distillation Pipeline Tests ─────────────────────────


class TestDreamPipeline:
    """Test the full Dream distillation pipeline with mock LLM."""

    def test_dream_no_messages(self):
        distiller, _ = _make_distiller()
        result = asyncio.run(distiller.dream(messages=[]))
        assert result.error == "No messages to distill"
        assert result.applied == 0

    def test_dream_empty_actions(self):
        distiller, _ = _make_distiller(llm_response="[]")
        messages = [{"role": "user", "content": "hello"}]
        result = asyncio.run(distiller.dream(messages=messages))
        assert result.applied == 0
        assert len(result.actions) == 0

    def test_dream_add_memory(self):
        """ADD action creates a new memory in the store."""
        response = json.dumps([
            {"action": "ADD", "content": "User works on OpenMate project",
             "memory_type": "episodic", "importance": 0.7,
             "tags": ["project"], "reason": "mentioned in conversation"}
        ])
        distiller, store = _make_distiller(llm_response=response)
        messages = [{"role": "user", "content": "I'm working on OpenMate"}]
        result = asyncio.run(distiller.dream(messages=messages))

        assert result.applied == 1
        # Verify memory was stored
        memories = store.list_memories()
        assert len(memories) == 1
        assert memories[0]["content"] == "User works on OpenMate project"
        assert memories[0]["memory_type"] == "episodic"

    def test_dream_update_memory(self):
        """UPDATE action modifies an existing memory."""
        # First, create a memory
        store = _make_store()
        mem = store.store(content="Old info", memory_type="semantic", importance=0.5)

        response = json.dumps([
            {"action": "UPDATE", "memory_id": mem.memory_id,
             "new_content": "New info from conversation", "reason": "updated"}
        ])
        distiller, _ = _make_distiller(store=store, llm_response=response)
        messages = [{"role": "user", "content": "Actually it's new info"}]
        result = asyncio.run(distiller.dream(messages=messages))

        assert result.applied == 1
        # Verify memory was updated
        updated = store.list_memories()
        assert updated[0]["content"] == "New info from conversation"

    def test_dream_delete_memory(self):
        """DELETE action soft-deletes an existing memory."""
        store = _make_store()
        mem = store.store(content="Outdated info", importance=0.3)

        response = json.dumps([
            {"action": "DELETE", "memory_id": mem.memory_id, "reason": "outdated"}
        ])
        distiller, _ = _make_distiller(store=store, llm_response=response)
        messages = [{"role": "user", "content": "That info is wrong"}]
        result = asyncio.run(distiller.dream(messages=messages))

        assert result.applied == 1
        # Memory should be soft-deleted (not in active list)
        active = store.list_memories()
        assert len(active) == 0

    def test_dream_mixed_actions(self):
        """Multiple actions in one Dream run."""
        store = _make_store()
        existing = store.store(content="Will be deleted", importance=0.2)

        response = json.dumps([
            {"action": "ADD", "content": "New fact", "importance": 0.6, "reason": "extracted"},
            {"action": "DELETE", "memory_id": existing.memory_id, "reason": "outdated"},
            {"action": "SKIP", "memory_id": "ltm_nonexistent", "reason": "keep"},
        ])
        distiller, _ = _make_distiller(store=store, llm_response=response)
        messages = [{"role": "user", "content": "test"}]
        result = asyncio.run(distiller.dream(messages=messages))

        assert result.applied == 3  # ADD + DELETE + SKIP all count as applied
        assert result.counts.get("ADD") == 1
        assert result.counts.get("DELETE") == 1
        assert result.counts.get("SKIP") == 1

    def test_dream_llm_error(self):
        """LLM call failure is captured, not raised."""
        async def failing_llm(sp, up):
            raise RuntimeError("provider down")

        store = _make_store()
        distiller = DreamDistiller(ltm_store=store, llm_call=failing_llm)
        messages = [{"role": "user", "content": "hello"}]
        result = asyncio.run(distiller.dream(messages=messages))

        assert "LLM call failed" in result.error
        assert result.applied == 0

    def test_dream_audit_trail(self):
        """Dream ADD operations write audit records (mem0 pattern)."""
        response = json.dumps([
            {"action": "ADD", "content": "Audited memory", "importance": 0.6,
             "reason": "distilled from conversation"}
        ])
        distiller, store = _make_distiller(llm_response=response)
        messages = [{"role": "user", "content": "test"}]
        asyncio.run(distiller.dream(messages=messages))

        # Check audit trail
        history = store.get_history(event="ADD")
        assert len(history) >= 1
        assert any("Audited memory" in h.get("new_value", "") for h in history)


# ── Dream Stats Tests ─────────────────────────────────────────


class TestDreamStats:
    """Test Dream distillation statistics tracking."""

    def test_initial_stats(self):
        distiller, _ = _make_distiller()
        stats = distiller.get_stats()
        assert stats["total_dreams"] == 0
        assert stats["total_actions_applied"] == 0
        assert stats["echo_blocked_count"] == 0

    def test_stats_after_dream(self):
        response = json.dumps([
            {"action": "ADD", "content": "test", "importance": 0.5, "reason": "x"},
        ])
        distiller, _ = _make_distiller(llm_response=response)
        messages = [{"role": "user", "content": "hello"}]
        asyncio.run(distiller.dream(messages=messages))

        stats = distiller.get_stats()
        assert stats["total_dreams"] == 1
        assert stats["total_actions_applied"] == 1

    def test_stats_tracks_echo_blocks(self):
        distiller, _ = _make_distiller()
        distiller.mark_recall(["ltm_abc"])
        messages = [{"role": "user", "content": "hello"}]
        asyncio.run(distiller.dream(messages=messages))

        stats = distiller.get_stats()
        assert stats["echo_blocked_count"] == 1
        assert stats["total_dreams"] == 1  # Dream was attempted but blocked


# ── Prompt Content Tests ──────────────────────────────────────


class TestDreamPrompt:
    """Verify the Dream system prompt contains key CowAgent patterns."""

    def test_prompt_has_anti_hallucination(self):
        """CowAgent anti-hallucination clause must be present."""
        assert "严禁编造推测" in DREAM_SYSTEM_PROMPT

    def test_prompt_has_five_steps(self):
        """All five CowAgent distillation steps must be listed."""
        assert "合并提炼" in DREAM_SYSTEM_PROMPT
        assert "新增萃取" in DREAM_SYSTEM_PROMPT
        assert "冲突更新" in DREAM_SYSTEM_PROMPT
        assert "清理无效" in DREAM_SYSTEM_PROMPT
        assert "删除冗余" in DREAM_SYSTEM_PROMPT

    def test_prompt_has_conservative_principle(self):
        """Conservative DELETE principle (use SKIP when uncertain)."""
        assert "SKIP" in DREAM_SYSTEM_PROMPT
        assert "保守" in DREAM_SYSTEM_PROMPT

    def test_prompt_has_memory_types(self):
        """All four memory types must be documented."""
        assert "episodic" in DREAM_SYSTEM_PROMPT
        assert "semantic" in DREAM_SYSTEM_PROMPT
        assert "procedural" in DREAM_SYSTEM_PROMPT
        assert "working" in DREAM_SYSTEM_PROMPT


# ── Integration: Echo Blocker + Dream Lifecycle ───────────────


class TestDreamLifecycle:
    """Full lifecycle: recall → echo block → reset → dream succeeds."""

    def test_full_lifecycle(self):
        response = json.dumps([
            {"action": "ADD", "content": "Lifecycle test memory",
             "importance": 0.6, "reason": "test"}
        ])
        distiller, store = _make_distiller(llm_response=response)
        messages = [{"role": "user", "content": "hello"}]

        # Turn 1: recall memories → echo blocker active
        distiller.mark_recall(["ltm_some"])
        result1 = asyncio.run(distiller.dream(messages=messages))
        assert result1.echo_blocked is True

        # Turn boundary: reset
        distiller.reset_turn()
        assert not distiller.should_skip_digest()

        # Turn 2: no recall → dream proceeds
        result2 = asyncio.run(distiller.dream(messages=messages))
        assert result2.echo_blocked is False
        assert result2.applied == 1

        # Verify both turns are in history
        stats = distiller.get_stats()
        assert stats["total_dreams"] == 2
        assert stats["echo_blocked_count"] == 1
