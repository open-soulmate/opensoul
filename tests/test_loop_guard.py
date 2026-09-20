"""Tests for LoopGuard — loop/repetition detection for agent safety.

Covers all detection strategies from the 5 source implementations:
- Tool repetition (ag2 consecutive + goose intervene threshold)
- Repeated combination (Khoj set-based detection)
- Text similarity (anything-llm 3-gram Jaccard)
- Progressive response (DeerFlow warn/intervene/force_stop)
- Cooldown mechanism (anything-llm 60s window)
"""

import time

import pytest

from src.cortex.loop_guard import (
    DetectionType,
    LoopDetectionResult,
    LoopGuard,
    LoopSeverity,
    ToolCallSignature,
)

# ── ToolCallSignature ───────────────────────────────────────────────────


class TestToolCallSignature:
    def test_from_dict_args(self):
        sig = ToolCallSignature.from_call("read_file", {"path": "/tmp/x"})
        assert sig.name == "read_file"
        assert len(sig.args_hash) == 12

    def test_from_string_args(self):
        sig = ToolCallSignature.from_call("bash", "ls -la")
        assert sig.name == "bash"

    def test_from_none_args(self):
        sig = ToolCallSignature.from_call("ping", None)
        assert sig.name == "ping"
        assert len(sig.args_hash) == 12

    def test_canonical_args_order(self):
        """Same args in different key order produce same hash."""
        sig1 = ToolCallSignature.from_call("f", {"a": 1, "b": 2})
        sig2 = ToolCallSignature.from_call("f", {"b": 2, "a": 1})
        assert sig1.args_hash == sig2.args_hash

    def test_different_args_different_hash(self):
        sig1 = ToolCallSignature.from_call("f", {"path": "/a"})
        sig2 = ToolCallSignature.from_call("f", {"path": "/b"})
        assert sig1.args_hash != sig2.args_hash

    def test_str_format(self):
        sig = ToolCallSignature.from_call("read", {"x": 1})
        assert str(sig).startswith("read:")


# ── Basic check behavior ────────────────────────────────────────────────


class TestBasicCheck:
    def test_empty_check_returns_ok(self):
        guard = LoopGuard()
        result = guard.check()
        assert result.severity == LoopSeverity.OK
        assert not result.is_looping

    def test_different_tools_no_loop(self):
        guard = LoopGuard()
        guard.check(tool_calls=[{"name": "read", "arguments": {"path": "/a"}}])
        guard.check(tool_calls=[{"name": "write", "arguments": {"path": "/b"}}])
        result = guard.check(tool_calls=[{"name": "bash", "arguments": {"cmd": "ls"}}])
        assert result.severity == LoopSeverity.OK

    def test_openai_format_parsing(self):
        """Support OpenAI function-calling format."""
        guard = LoopGuard()
        guard.check(tool_calls=[{"function": {"name": "search", "arguments": {"q": "x"}}}])
        guard.check(tool_calls=[{"function": {"name": "search", "arguments": {"q": "x"}}}])
        result = guard.check(tool_calls=[{"function": {"name": "search", "arguments": {"q": "x"}}}])
        # 3rd consecutive same call → warn
        assert result.severity == LoopSeverity.WARN
        assert result.detection_type == DetectionType.TOOL_REPETITION

    def test_reset_clears_state(self):
        guard = LoopGuard()
        for _ in range(4):
            guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        guard.reset()
        assert guard.stats["round"] == 0
        assert guard.stats["window_size"] == 0
        assert guard.stats["flagged_count"] == 0


# ── Tool repetition (ag2 + goose) ───────────────────────────────────────


class TestToolRepetition:
    def test_consecutive_warn_at_threshold(self):
        """3 consecutive identical calls → WARN (ag2 threshold=3)."""
        guard = LoopGuard(tool_repeat_warn=3)
        guard.check(tool_calls=[{"name": "search", "arguments": {"q": "test"}}])
        guard.check(tool_calls=[{"name": "search", "arguments": {"q": "test"}}])
        result = guard.check(tool_calls=[{"name": "search", "arguments": {"q": "test"}}])
        assert result.severity == LoopSeverity.WARN
        assert result.detection_type == DetectionType.TOOL_REPETITION
        assert result.consecutive_count == 3
        assert "search" in result.message

    def test_consecutive_intervene_at_threshold(self):
        """5 consecutive identical calls → INTERVENE (goose max_repetitions)."""
        guard = LoopGuard(tool_repeat_warn=3, tool_repeat_intervene=5)
        # First 2: OK
        guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        # 3rd: WARN
        result = guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        assert result.severity == LoopSeverity.WARN
        # 4th: still OK (warned already, in flagged set)
        result = guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        assert result.severity == LoopSeverity.OK
        # 5th: INTERVENE
        result = guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        assert result.severity == LoopSeverity.INTERVENE
        assert result.suggested_action == "strip_tool_calls"

    def test_warn_only_fires_once_per_signature(self):
        """ag2 _flagged dedup: same signature only warned once."""
        guard = LoopGuard(tool_repeat_warn=3, tool_repeat_intervene=100)
        guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        result1 = guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        assert result1.severity == LoopSeverity.WARN
        # Continue calling same tool — no second warn
        result2 = guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        assert result2.severity == LoopSeverity.OK  # already flagged

    def test_different_args_break_consecutive(self):
        """Different arguments reset the consecutive counter."""
        guard = LoopGuard(tool_repeat_warn=3)
        guard.check(tool_calls=[{"name": "read", "arguments": {"path": "/a"}}])
        guard.check(tool_calls=[{"name": "read", "arguments": {"path": "/b"}}])
        guard.check(tool_calls=[{"name": "read", "arguments": {"path": "/a"}}])
        result = guard.check(tool_calls=[{"name": "read", "arguments": {"path": "/c"}}])
        assert result.severity == LoopSeverity.OK


# ── Repeated combination (Khoj) ─────────────────────────────────────────


class TestRepeatedCombination:
    def test_non_consecutive_repeat_warns(self):
        """Same tool+args called again after other calls → WARN."""
        guard = LoopGuard()
        guard.check(tool_calls=[{"name": "search", "arguments": {"q": "x"}}])
        guard.check(tool_calls=[{"name": "read", "arguments": {"path": "/a"}}])
        guard.check(tool_calls=[{"name": "write", "arguments": {"path": "/b"}}])
        # Same search call again → Khoj warning
        result = guard.check(tool_calls=[{"name": "search", "arguments": {"q": "x"}}])
        assert result.severity == LoopSeverity.WARN
        assert result.detection_type == DetectionType.REPEATED_COMBINATION
        assert "already called" in result.message

    def test_different_args_not_flagged(self):
        """Same tool with different args → no warning."""
        guard = LoopGuard()
        guard.check(tool_calls=[{"name": "search", "arguments": {"q": "x"}}])
        guard.check(tool_calls=[{"name": "read", "arguments": {"path": "/a"}}])
        result = guard.check(tool_calls=[{"name": "search", "arguments": {"q": "y"}}])
        assert result.severity == LoopSeverity.OK

    def test_repeat_only_warns_once(self):
        """Same combination repeated multiple times only warns once (Khoj dedup)."""
        guard = LoopGuard(tool_repeat_intervene=100)  # prevent intervene
        guard.check(tool_calls=[{"name": "t", "arguments": {"x": 1}}])
        # Different call in between (non-consecutive repeat)
        guard.check(tool_calls=[{"name": "other", "arguments": {"y": 2}}])
        result1 = guard.check(tool_calls=[{"name": "t", "arguments": {"x": 1}}])
        assert result1.severity == LoopSeverity.WARN
        assert result1.detection_type == DetectionType.REPEATED_COMBINATION
        # Another non-consecutive repeat — should NOT warn again (already flagged)
        guard.check(tool_calls=[{"name": "other2", "arguments": {"z": 3}}])
        result2 = guard.check(tool_calls=[{"name": "t", "arguments": {"x": 1}}])
        assert result2.severity == LoopSeverity.OK  # already flagged


# ── Text similarity (anything-llm) ──────────────────────────────────────


class TestTextSimilarity:
    def test_similar_text_detection(self):
        """Highly similar text outputs trigger force_stop."""
        guard = LoopGuard(text_repeat_threshold=4, cooldown_seconds=0)
        text = "I will now search for the file and read its contents to understand the structure."
        for _ in range(5):
            result = guard.check(text_response=text)
        # Should eventually trigger
        assert result.severity == LoopSeverity.FORCE_STOP
        assert result.detection_type == DetectionType.TEXT_SIMILARITY

    def test_different_text_no_loop(self):
        """Different text outputs don't trigger."""
        guard = LoopGuard()
        guard.check(text_response="First I will analyze the requirements carefully.")
        guard.check(text_response="Next, let me check the database schema.")
        result = guard.check(text_response="Finally, I'll implement the solution.")
        assert result.severity == LoopSeverity.OK

    def test_short_text_ignored(self):
        """Very short text is not checked (insufficient for shingles)."""
        guard = LoopGuard()
        for _ in range(20):
            result = guard.check(text_response="ok")
        assert result.severity == LoopSeverity.OK

    def test_jaccard_similarity_calculation(self):
        """Verify Jaccard similarity computation is correct."""
        set_a = {"abc", "bcd", "cde", "def"}
        set_b = {"abc", "bcd", "cde", "efg"}
        # Intersection: 3, Union: 5 → 0.6
        sim = LoopGuard._jaccard_similarity(set_a, set_b)
        assert abs(sim - 0.6) < 0.01

    def test_shingle_extraction(self):
        """3-gram shingles are correctly extracted."""
        shingles = LoopGuard._extract_shingles("hello world")
        assert "hel" in shingles
        assert "ell" in shingles
        assert "llo" in shingles
        assert len(shingles) == len("hello world") - 3 + 1


# ── Cooldown mechanism (anything-llm) ───────────────────────────────────


class TestCooldown:
    def test_cooldown_suppresses_alarms(self):
        """After alarm fires, subsequent alarms suppressed for cooldown period."""
        guard = LoopGuard(tool_repeat_intervene=3, cooldown_seconds=60.0)
        # Trigger first alarm
        guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        result1 = guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        assert result1.severity == LoopSeverity.INTERVENE
        # Immediately try again — should be suppressed by cooldown
        guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        result2 = guard.check(tool_calls=[{"name": "t", "arguments": {}}])
        # During cooldown, intervene is suppressed
        assert (
            result2.severity != LoopSeverity.INTERVENE
            or result2.detection_type != DetectionType.TOOL_REPETITION
        )


# ── Stats (observability) ───────────────────────────────────────────────


class TestStats:
    def test_stats_tracking(self):
        """Guard tracks internal statistics correctly."""
        guard = LoopGuard()
        guard.check(tool_calls=[{"name": "a", "arguments": {"x": 1}}])
        guard.check(tool_calls=[{"name": "b", "arguments": {"y": 2}}])
        guard.check(text_response="Some response text here.")
        stats = guard.stats
        assert stats["round"] == 3
        assert stats["window_size"] == 2  # two tool calls in window
        assert stats["unique_combinations"] == 2
        assert stats["recent_texts"] == 1

    def test_stats_after_reset(self):
        guard = LoopGuard()
        guard.check(tool_calls=[{"name": "a", "arguments": {}}])
        guard.reset()
        stats = guard.stats
        assert stats["round"] == 0
        assert stats["window_size"] == 0
        assert stats["unique_combinations"] == 0
        assert stats["flagged_count"] == 0
