"""Tests for the langfuse-style three-layer observability model in
trajectory/store.py: Trace → Observation (nested spans) → Score.

Runs against a real temporary SQLite database (no mocks) so the SQL,
the end_at migration, and the tree/rollup logic are actually exercised.
"""

from contextlib import asynccontextmanager

import pytest

from src.config import settings
from src.database.postgres import db_pool
from src.trajectory.store import (
    EventType,
    ScoreDataType,
    TrajectoryEvent,
    TrajectoryScore,
    TrajectoryStore,
    gen_ai_attributes,
)


@asynccontextmanager
async def temp_store(tmp_path):
    """TrajectoryStore backed by a fresh SQLite file."""
    old_url = settings.database_url
    settings.database_url = f"sqlite:///{tmp_path}/traj_test.db"
    await db_pool.connect()
    try:
        yield TrajectoryStore()
    finally:
        await db_pool.disconnect()
        settings.database_url = old_url


async def _mk_event(store, session_id, event_type="span", parent=None, tokens=0, content=""):
    ev = TrajectoryEvent(
        session_id=session_id,
        parent_event_id=parent,
        event_type=event_type,
        content=content,
        token_usage=tokens,
    )
    return await store.add_event(ev)


# ── Span close (end_event) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_end_event_stamps_end_at_and_duration(tmp_path):
    async with temp_store(tmp_path) as store:
        session = await store.create_session(agent_id="a1")
        ev = await _mk_event(store, session.id, EventType.LLM_CALL.value, tokens=0)
        closed = await store.end_event(ev.id, status="ok", token_usage=42)
        assert closed is not None
        assert closed.end_at is not None
        assert closed.duration_ms >= 0
        assert closed.status == "ok"
        assert closed.token_usage == 42
        # persisted
        reread = await store.get_event(ev.id)
        assert reread is not None
        assert reread.end_at == closed.end_at
        assert reread.token_usage == 42
        # session token total folded in
        s = await store.get_session(session.id)
        assert s is not None
        assert s.total_tokens == 42


@pytest.mark.asyncio
async def test_end_event_without_token_update_keeps_total(tmp_path):
    async with temp_store(tmp_path) as store:
        session = await store.create_session()
        ev = await _mk_event(store, session.id, tokens=10)
        closed = await store.end_event(ev.id)
        assert closed is not None
        assert closed.token_usage == 10
        s = await store.get_session(session.id)
        assert s is not None
        assert s.total_tokens == 10


@pytest.mark.asyncio
async def test_end_event_unknown_id_returns_none(tmp_path):
    async with temp_store(tmp_path) as store:
        assert await store.end_event("nope") is None


@pytest.mark.asyncio
async def test_ensure_tables_migration_idempotent(tmp_path):
    async with temp_store(tmp_path) as store:
        # ensure_tables runs repeatedly — the end_at ALTER must not raise
        await store.ensure_tables()
        await store.ensure_tables()
        session = await store.create_session()
        ev = await _mk_event(store, session.id)
        ev2 = TrajectoryEvent(session_id=session.id, end_at="2026-01-01T00:00:00+00:00")
        await store.add_event(ev2)
        reread = await store.get_event(ev2.id)
        assert reread is not None
        assert reread.end_at == "2026-01-01T00:00:00+00:00"
        assert ev.id != ev2.id


# ── Trace tree (get_trace_tree) ────────────────────────────────


@pytest.mark.asyncio
async def test_trace_tree_nesting_and_rollups(tmp_path):
    async with temp_store(tmp_path) as store:
        session = await store.create_session()
        root = await _mk_event(store, session.id, EventType.LLM_CALL.value, tokens=100)
        child = await _mk_event(
            store, session.id, EventType.TOOL_CALL.value, parent=root.id, tokens=20
        )
        grandchild = await _mk_event(
            store, session.id, EventType.TOOL_RESULT.value, parent=child.id, tokens=5
        )
        other_root = await _mk_event(store, session.id, EventType.USER_INPUT.value, tokens=0)

        tree = await store.get_trace_tree(session.id)
        assert tree["total_events"] == 4
        assert tree["max_depth"] == 2
        assert len(tree["roots"]) == 2

        root_node = next(r for r in tree["roots"] if r["id"] == root.id)
        assert root_node["depth"] == 0
        assert root_node["subtree_events"] == 3
        assert root_node["subtree_tokens"] == 125
        assert len(root_node["children"]) == 1

        child_node = root_node["children"][0]
        assert child_node["id"] == child.id
        assert child_node["depth"] == 1
        assert child_node["subtree_tokens"] == 25
        assert child_node["children"][0]["id"] == grandchild.id
        assert child_node["children"][0]["depth"] == 2

        other = next(r for r in tree["roots"] if r["id"] == other_root.id)
        assert other["subtree_events"] == 1


@pytest.mark.asyncio
async def test_trace_tree_dangling_parent_promoted_to_root(tmp_path):
    async with temp_store(tmp_path) as store:
        session = await store.create_session()
        ev = await _mk_event(store, session.id, parent="missing-parent")
        tree = await store.get_trace_tree(session.id)
        assert len(tree["roots"]) == 1
        assert tree["roots"][0]["id"] == ev.id


@pytest.mark.asyncio
async def test_trace_tree_cycle_does_not_hang(tmp_path):
    async with temp_store(tmp_path) as store:
        session = await store.create_session()
        a = await _mk_event(store, session.id)
        b = await _mk_event(store, session.id, parent=a.id)
        # corrupt lineage: a's parent becomes b → 2-cycle
        await db_pool.execute(
            "UPDATE trajectory_events SET parent_event_id = ? WHERE id = ?", b.id, a.id
        )
        tree = await store.get_trace_tree(session.id)
        # both cycle members end up as roots; no infinite loop
        assert {r["id"] for r in tree["roots"]} == {a.id, b.id}


# ── Scores (Score layer) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_add_and_get_scores(tmp_path):
    async with temp_store(tmp_path) as store:
        session = await store.create_session()
        ev = await _mk_event(store, session.id)
        s1 = TrajectoryScore(
            session_id=session.id, name="correctness", value=8.0, source="llm_judge"
        )
        s2 = TrajectoryScore(
            session_id=session.id,
            trace_id=ev.id,
            name="correctness",
            value=9.0,
            source="human",
            comment="manual review",
        )
        s3 = TrajectoryScore(
            session_id=session.id,
            name="passed",
            value=1.0,
            data_type=ScoreDataType.BOOLEAN.value,
            source="code_eval",
        )
        await store.add_score(s1)
        await store.add_score(s2)
        await store.add_score(s3)

        scores = await store.get_scores(session_id=session.id)
        assert len(scores) == 3
        assert {s.name for s in scores} == {"correctness", "passed"}
        assert all(s.id for s in scores)

        human_only = await store.get_scores(session_id=session.id, source="human")
        assert len(human_only) == 1
        assert human_only[0].comment == "manual review"
        assert human_only[0].trace_id == ev.id


@pytest.mark.asyncio
async def test_score_validation(tmp_path):
    async with temp_store(tmp_path) as store:
        session = await store.create_session()
        with pytest.raises(ValueError):
            await store.add_score(TrajectoryScore(session_id=session.id, name=""))
        with pytest.raises(ValueError):
            await store.add_score(TrajectoryScore(session_id="", name="x"))
        with pytest.raises(ValueError):
            await store.add_score(
                TrajectoryScore(session_id=session.id, name="x", data_type="stars")
            )
        with pytest.raises(ValueError):
            await store.add_score(
                TrajectoryScore(session_id=session.id, name="x", source="anonymous")
            )


@pytest.mark.asyncio
async def test_score_summary_aggregation(tmp_path):
    async with temp_store(tmp_path) as store:
        session = await store.create_session()
        for value, source in [(7.0, "llm_judge"), (9.0, "human"), (8.0, "api")]:
            await store.add_score(
                TrajectoryScore(session_id=session.id, name="quality", value=value, source=source)
            )
        await store.add_score(
            TrajectoryScore(session_id=session.id, name="latency", value=1200.0, source="api")
        )
        summary = await store.get_score_summary(session_id=session.id)
        assert summary["total_score_names"] == 2
        by_name = {s["name"]: s for s in summary["scores"]}
        q = by_name["quality"]
        assert q["count"] == 3
        assert q["avg_value"] == 8.0
        assert q["min_value"] == 7.0
        assert q["max_value"] == 9.0
        assert q["llm_judge_count"] == 1
        assert q["human_count"] == 1
        assert by_name["latency"]["count"] == 1


@pytest.mark.asyncio
async def test_delete_session_removes_scores(tmp_path):
    async with temp_store(tmp_path) as store:
        session = await store.create_session()
        await store.add_score(TrajectoryScore(session_id=session.id, name="q", value=1.0))
        await store.delete_session(session.id)
        assert await store.get_scores(session_id=session.id) == []


# ── gen_ai semconv helper ──────────────────────────────────────


def test_gen_ai_attributes():
    attrs = gen_ai_attributes(
        system="openai",
        model="gpt-4o",
        input_tokens=100,
        output_tokens=50,
        finish_reason="stop",
    )
    assert attrs["gen_ai.system"] == "openai"
    assert attrs["gen_ai.request.model"] == "gpt-4o"
    assert attrs["gen_ai.usage.input_tokens"] == 100
    assert attrs["gen_ai.usage.output_tokens"] == 50
    assert attrs["gen_ai.response.finish_reasons"] == ["stop"]


def test_gen_ai_attributes_minimal():
    attrs = gen_ai_attributes(system="ollama", model="qwen3")
    assert attrs == {"gen_ai.system": "ollama", "gen_ai.request.model": "qwen3"}
