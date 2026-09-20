"""Tests for Khoj-style natural language memory search filters (P0-6).

Covers:
- DateFilter: relative dates (today/yesterday/last week/N days ago/recent)
- DateFilter: absolute dates (after/before YYYY-MM-DD, in YYYY-MM, in YYYY)
- TypeFilter: episodic/semantic/procedural/working
- ImportanceFilter: important/critical/high importance
- WordFilter: mentioning/containing/about/related to
- Combined filters in single query
- Clean query extraction (filter phrases stripped)
- apply_post_filters: date/importance/word filtering on memory dicts
- Edge cases: empty query, pure filter query, Chinese text
"""

import json
import time
from datetime import datetime, timedelta

import pytest

from src.hippo.nl_filters import (
    NLFilterResult,
    _compute_date_range,
    _fmt_ts,
    apply_post_filters,
    parse_nl_query,
)

# ── DateFilter: relative dates ────────────────────────────────────


class TestRelativeDates:
    def test_today(self):
        now = time.time()
        r = parse_nl_query("today memories about coding", now=now)
        assert r.date_from > 0
        assert r.date_to > 0
        dt = datetime.fromtimestamp(r.date_from)
        assert dt.hour == 0 and dt.minute == 0  # start of day

    def test_yesterday(self):
        now = time.time()
        r = parse_nl_query("what happened yesterday", now=now)
        assert r.date_from > 0
        dt_from = datetime.fromtimestamp(r.date_from)
        dt_now = datetime.fromtimestamp(now)
        assert dt_from.day == (dt_now - timedelta(days=1)).day

    def test_last_week(self):
        now = time.time()
        r = parse_nl_query("memories from last week", now=now)
        assert r.date_from > 0
        expected_from = now - 7 * 86400
        # Allow up to 1 day slack for day-boundary rounding
        assert abs(r.date_from - expected_from) < 86400

    def test_last_month(self):
        now = time.time()
        r = parse_nl_query("last month entries", now=now)
        assert r.date_from > 0
        expected_from = now - 30 * 86400
        assert abs(r.date_from - expected_from) < 2 * 86400

    def test_n_days_ago(self):
        now = time.time()
        r = parse_nl_query("3 days ago I learned something", now=now)
        assert r.date_from > 0
        expected_from = now - 3 * 86400
        assert abs(r.date_from - expected_from) < 86400

    def test_recent(self):
        now = time.time()
        r = parse_nl_query("recent discussions", now=now)
        assert r.date_from > 0
        assert r.date_from > now - 8 * 86400  # within last 7 days + slack

    def test_last_n_weeks(self):
        now = time.time()
        r = parse_nl_query("from last 2 weeks", now=now)
        assert r.date_from > 0
        expected_from = now - 14 * 86400
        assert abs(r.date_from - expected_from) < 86400

    def test_no_date_in_plain_query(self):
        r = parse_nl_query("python programming tips")
        assert r.date_from == 0
        assert r.date_to == 0


class TestAbsoluteDates:
    def test_after_date(self):
        r = parse_nl_query("memories after 2024-06-15 about the project")
        assert r.date_from > 0
        expected = datetime(2024, 6, 15).timestamp()
        assert abs(r.date_from - expected) < 1

    def test_before_date(self):
        r = parse_nl_query("before 2024-03-01 I was learning Rust")
        assert r.date_to > 0
        expected = datetime(2024, 3, 1).timestamp()
        assert abs(r.date_to - expected) < 1

    def test_since_date(self):
        r = parse_nl_query("since 2024-01-01 progress notes")
        assert r.date_from > 0
        expected = datetime(2024, 1, 1).timestamp()
        assert abs(r.date_from - expected) < 1

    def test_in_month(self):
        r = parse_nl_query("in 2024-03 I traveled")
        assert r.date_from > 0
        assert r.date_to > 0
        expected_start = datetime(2024, 3, 1).timestamp()
        assert abs(r.date_from - expected_start) < 1
        expected_end = datetime(2024, 4, 1).timestamp()
        assert abs(r.date_to - expected_end) < 1

    def test_in_year(self):
        r = parse_nl_query("in 2023 I built a project")
        assert r.date_from > 0
        assert r.date_to > 0
        expected_start = datetime(2023, 1, 1).timestamp()
        expected_end = datetime(2024, 1, 1).timestamp()
        assert abs(r.date_from - expected_start) < 1
        assert abs(r.date_to - expected_end) < 1


# ── TypeFilter ─────────────────────────────────────────────────────


class TestTypeFilter:
    def test_episodic(self):
        r = parse_nl_query("episodic memories about travel")
        assert "episodic" in r.memory_types

    def test_semantic(self):
        r = parse_nl_query("semantic knowledge about Python")
        assert "semantic" in r.memory_types

    def test_procedural(self):
        r = parse_nl_query("procedural memories for deployment")
        assert "procedural" in r.memory_types

    def test_working(self):
        r = parse_nl_query("working memory context")
        assert "working" in r.memory_types

    def test_no_type_in_plain_query(self):
        r = parse_nl_query("tell me about the weather")
        assert r.memory_types == []

    def test_type_excluded_from_clean_query(self):
        r = parse_nl_query("episodic memories about my trip")
        assert "episodic" not in r.clean_query.lower()


# ── ImportanceFilter ───────────────────────────────────────────────


class TestImportanceFilter:
    def test_important(self):
        r = parse_nl_query("important memories about security")
        assert r.min_importance >= 0.6

    def test_critical(self):
        r = parse_nl_query("critical decisions made last month")
        assert r.min_importance >= 0.8

    def test_high_importance(self):
        r = parse_nl_query("high importance lessons learned")
        assert r.min_importance >= 0.6

    def test_no_importance_in_plain_query(self):
        r = parse_nl_query("normal search query")
        assert r.min_importance == 0.0


# ── WordFilter ─────────────────────────────────────────────────────


class TestWordFilter:
    def test_mentioning(self):
        r = parse_nl_query("memories mentioning Docker")
        assert "Docker" in r.word_filters

    def test_containing(self):
        r = parse_nl_query("entries containing Kubernetes")
        assert "Kubernetes" in r.word_filters

    def test_about(self):
        r = parse_nl_query("memories about Python from last week")
        # "about Python" should be captured as word filter
        assert any("Python" in wf for wf in r.word_filters)

    def test_related_to(self):
        r = parse_nl_query("notes related to machine learning")
        assert any("machine learning" in wf.lower() for wf in r.word_filters)

    def test_no_word_filter_in_plain_query(self):
        r = parse_nl_query("just a regular search")
        assert r.word_filters == []


# ── Combined filters ───────────────────────────────────────────────


class TestCombinedFilters:
    def test_date_and_type(self):
        now = time.time()
        r = parse_nl_query("episodic memories from last week", now=now)
        assert "episodic" in r.memory_types
        assert r.date_from > 0

    def test_date_and_word(self):
        r = parse_nl_query("memories mentioning Docker after 2024-01-01")
        assert any("Docker" in wf for wf in r.word_filters)
        assert r.date_from > 0

    def test_type_and_importance(self):
        r = parse_nl_query("important semantic memories about AI")
        assert "semantic" in r.memory_types
        assert r.min_importance >= 0.6
        assert any("AI" in wf for wf in r.word_filters)

    def test_all_filters(self):
        now = time.time()
        r = parse_nl_query(
            "important episodic memories mentioning Python from last month",
            now=now,
        )
        assert "episodic" in r.memory_types
        assert r.min_importance >= 0.6
        assert any("Python" in wf for wf in r.word_filters)
        assert r.date_from > 0
        assert r.has_filters


# ── Clean query extraction ─────────────────────────────────────────


class TestCleanQuery:
    def test_filters_stripped(self):
        r = parse_nl_query("episodic memories from last week about Docker")
        # Clean query should not contain filter phrases
        assert "episodic" not in r.clean_query.lower()
        assert "last week" not in r.clean_query.lower()

    def test_clean_query_preserves_search_terms(self):
        r = parse_nl_query("important memories about Python programming")
        # "about Python programming" is captured as word_filter — the terms
        # are preserved in the filter, not the clean query. This is correct:
        # the search system uses word_filters for content matching.
        assert (
            any("python" in wf.lower() for wf in r.word_filters)
            or "python" in r.clean_query.lower()
        )

    def test_empty_query(self):
        r = parse_nl_query("")
        assert r.clean_query == ""
        assert not r.has_filters

    def test_pure_filter_query(self):
        r = parse_nl_query("important memories from last week")
        # Clean query may be empty — that's fine, has_filters is True
        assert r.has_filters

    def test_no_filters_clean_equals_original(self):
        r = parse_nl_query("Python async context managers")
        # Without any filters, clean query should be close to original
        assert "Python" in r.clean_query or "python" in r.clean_query.lower()
        assert "async" in r.clean_query.lower()


# ── NLFilterResult ─────────────────────────────────────────────────


class TestNLFilterResult:
    def test_empty_result(self):
        r = NLFilterResult()
        assert not r.has_filters
        assert r.clean_query == ""

    def test_to_dict(self):
        r = parse_nl_query("important memories about Python")
        d = r.to_dict()
        assert "clean_query" in d
        assert "date_from" in d
        assert "memory_types" in d
        assert "min_importance" in d
        assert "word_filters" in d
        assert "parsed_filters" in d
        assert "has_filters" in d

    def test_parsed_filters_log(self):
        r = parse_nl_query("episodic memories from last week about Docker")
        assert len(r.parsed_filters) > 0
        # At least date and type should be logged
        joined = " ".join(r.parsed_filters)
        assert "date" in joined.lower() or "episodic" in joined.lower()


# ── apply_post_filters ─────────────────────────────────────────────


def _make_memory(created_at=None, importance=0.5, content="", tags=None):
    return {
        "memory_id": f"mem_{hash(content) % 10000}",
        "content": content,
        "memory_type": "episodic",
        "importance": importance,
        "tags": tags or [],
        "created_at": created_at or time.time(),
    }


class TestApplyPostFilters:
    def test_no_filters_passthrough(self):
        memories = [_make_memory(content="a"), _make_memory(content="b")]
        nl = NLFilterResult()
        result = apply_post_filters(memories, nl)
        assert len(result) == 2

    def test_date_from_filter(self):
        now = time.time()
        old = _make_memory(created_at=now - 30 * 86400, content="old memory")
        new = _make_memory(created_at=now - 2 * 86400, content="new memory")
        nl = NLFilterResult(date_from=now - 7 * 86400)
        result = apply_post_filters([old, new], nl)
        assert len(result) == 1
        assert result[0]["content"] == "new memory"

    def test_date_to_filter(self):
        now = time.time()
        old = _make_memory(created_at=now - 30 * 86400, content="old memory")
        new = _make_memory(created_at=now - 2 * 86400, content="new memory")
        nl = NLFilterResult(date_to=now - 7 * 86400)
        result = apply_post_filters([old, new], nl)
        assert len(result) == 1
        assert result[0]["content"] == "old memory"

    def test_importance_filter(self):
        low = _make_memory(importance=0.3, content="low importance")
        high = _make_memory(importance=0.8, content="high importance")
        nl = NLFilterResult(min_importance=0.6)
        result = apply_post_filters([low, high], nl)
        assert len(result) == 1
        assert result[0]["content"] == "high importance"

    def test_word_filter(self):
        docker = _make_memory(content="learned about Docker containers")
        k8s = _make_memory(content="studied Kubernetes orchestration")
        nl = NLFilterResult(word_filters=["Docker"])
        result = apply_post_filters([docker, k8s], nl)
        assert len(result) == 1
        assert "Docker" in result[0]["content"]

    def test_word_filter_case_insensitive(self):
        mem = _make_memory(content="learned about docker containers")
        nl = NLFilterResult(word_filters=["Docker"])
        result = apply_post_filters([mem], nl)
        assert len(result) == 1

    def test_word_filter_in_tags(self):
        mem = _make_memory(content="some note", tags=["docker", "devops"])
        nl = NLFilterResult(word_filters=["docker"])
        result = apply_post_filters([mem], nl)
        assert len(result) == 1

    def test_multiple_word_filters_all_required(self):
        both = _make_memory(content="Docker and Kubernetes together")
        only_docker = _make_memory(content="just Docker")
        nl = NLFilterResult(word_filters=["Docker", "Kubernetes"])
        result = apply_post_filters([both, only_docker], nl)
        assert len(result) == 1
        assert "Kubernetes" in result[0]["content"]

    def test_combined_date_and_importance(self):
        now = time.time()
        recent_low = _make_memory(created_at=now - 86400, importance=0.2, content="recent low")
        recent_high = _make_memory(created_at=now - 86400, importance=0.9, content="recent high")
        old_high = _make_memory(created_at=now - 30 * 86400, importance=0.9, content="old high")
        nl = NLFilterResult(date_from=now - 7 * 86400, min_importance=0.6)
        result = apply_post_filters([recent_low, recent_high, old_high], nl)
        assert len(result) == 1
        assert result[0]["content"] == "recent high"


# ── Helper functions ───────────────────────────────────────────────


class TestHelpers:
    def test_compute_date_range_day(self):
        now = time.time()
        df, dt = _compute_date_range("day", 1, now)
        assert df > 0
        assert dt == now
        d = datetime.fromtimestamp(df)
        assert d.hour == 0

    def test_compute_date_range_week(self):
        now = time.time()
        df, dt = _compute_date_range("week", 2, now)
        assert df > 0
        expected = now - 14 * 86400
        assert abs(df - expected) < 86400

    def test_compute_date_range_unknown_unit(self):
        df, dt = _compute_date_range("century", 1)
        assert df == 0
        assert dt == 0

    def test_fmt_ts_zero(self):
        assert _fmt_ts(0) == "—"

    def test_fmt_ts_valid(self):
        ts = datetime(2024, 6, 15, 10, 30).timestamp()
        result = _fmt_ts(ts)
        assert "2024-06-15" in result


# ── Chinese text handling ──────────────────────────────────────────


class TestChineseText:
    def test_chinese_query_no_filters(self):
        r = parse_nl_query("Python编程技巧")
        assert not r.has_filters
        assert len(r.clean_query) > 0

    def test_chinese_with_english_filter(self):
        r = parse_nl_query("important memories 关于Python的学习")
        assert r.min_importance > 0

    def test_word_filter_chinese(self):
        mem = _make_memory(content="学习了Docker容器技术")
        nl = NLFilterResult(word_filters=["Docker"])
        result = apply_post_filters([mem], nl)
        assert len(result) == 1
