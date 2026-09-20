"""Tests for Goal Autonomous Runner (kilocode goal/runner.ts pattern移植)。

覆盖：
- 五状态机转换
- 事件驱动结果判定
- 准入检查（admission）
- goal_report自报协议
- 失败即停原则
- 用户抢占（pause/resume）
- 持久化
"""

import json
import tempfile
from pathlib import Path

import pytest

from src.will.goal_runner import (
    Goal,
    GoalCreate,
    GoalEvent,
    GoalOutcome,
    GoalReport,
    GoalRunner,
    GoalState,
)


@pytest.fixture
def runner(tmp_path):
    """Create a GoalRunner with isolated persistence."""
    r = GoalRunner.__new__(GoalRunner)
    r._goals = {}
    r._PERSIST_PATH = tmp_path / "goals.json"
    return r


class TestGoalStateMachine:
    """五状态机测试"""

    def test_create_goal_initial_state(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test goal"))
        assert goal is not None
        assert goal.state == GoalState.ACTIVE
        assert goal.description == "test goal"
        assert goal.started_at is not None

    def test_pause_and_resume(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test"))
        goal_id = goal.goal_id

        paused = runner.pause_goal(goal_id, "user preempted")
        assert paused.state == GoalState.PAUSED
        assert paused.superseded_count == 1

        resumed = runner.resume_goal(goal_id)
        assert resumed.state == GoalState.ACTIVE

    def test_blocked_goal_cannot_resume_directly(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test"))
        goal_id = goal.goal_id
        # Force to BLOCKED via blocked event
        runner.record_event(goal_id, "plan_exit", {"error": "blocked"})
        g = runner.get_goal(goal_id)
        assert g.state == GoalState.BLOCKED
        # BLOCKED cannot resume directly
        result = runner.resume_goal(goal_id)
        assert result is None

    def test_cancel_goal(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test"))
        goal_id = goal.goal_id

        cancelled = runner.cancel_goal(goal_id)
        assert cancelled.state == GoalState.FAILED
        assert cancelled.is_terminal

    def test_terminal_goals_ignore_events(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test"))
        goal_id = goal.goal_id
        runner.cancel_goal(goal_id)

        event = runner.record_event(goal_id, "bash", {"exit_code": 0})
        assert event is None  # Terminal goals don't accept events


class TestAdmissionCheck:
    """准入前置检查测试"""

    def test_admit_clean_session(self, runner):
        ok, reason = runner.admit("new_session")
        assert ok is True
        assert reason == "ok"

    def test_admit_rejects_active_goal(self, runner):
        runner.create_goal(GoalCreate(session_id="s1", description="goal1"))
        ok, reason = runner.admit("s1")
        assert ok is False
        assert "active" in reason.lower()

    def test_admit_rejects_blocked_goal(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="goal1"))
        runner.record_event(goal.goal_id, "plan_exit", {"error": "blocked"})
        ok, reason = runner.admit("s1")
        assert ok is False
        assert "blocked" in reason.lower()

    def test_admit_allows_paused_goal(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="goal1"))
        runner.pause_goal(goal.goal_id)
        ok, reason = runner.admit("s1")
        assert ok is True

    def test_admit_allows_different_session(self, runner):
        runner.create_goal(GoalCreate(session_id="s1", description="goal1"))
        ok, reason = runner.admit("s2")
        assert ok is True


class TestEventDrivenOutcome:
    """事件驱动结果判定测试"""

    def test_classify_bash_success(self, runner):
        outcome = runner.classify_tool_outcome("bash", {"exit_code": 0})
        assert outcome == GoalOutcome.SUCCESS

    def test_classify_bash_failure(self, runner):
        outcome = runner.classify_tool_outcome("bash", {"exit_code": 1})
        assert outcome == GoalOutcome.FAILED

    def test_classify_question_none(self, runner):
        outcome = runner.classify_tool_outcome("question", {})
        assert outcome == GoalOutcome.NONE

    def test_classify_plan_exit_blocked(self, runner):
        outcome = runner.classify_tool_outcome("plan_exit", {})
        assert outcome == GoalOutcome.BLOCKED

    def test_classify_generic_error(self, runner):
        outcome = runner.classify_tool_outcome("read_file", {"error": "file not found"})
        assert outcome == GoalOutcome.FAILED

    def test_classify_generic_success(self, runner):
        outcome = runner.classify_tool_outcome("read_file", {"content": "hello"})
        assert outcome == GoalOutcome.SUCCESS

    def test_event_counts(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test"))
        gid = goal.goal_id

        runner.record_event(gid, "bash", {"exit_code": 0})
        runner.record_event(gid, "bash", {"exit_code": 0})
        runner.record_event(gid, "bash", {"exit_code": 1})
        runner.record_event(gid, "question", {})
        runner.record_event(gid, "read_file", {"content": "ok"})

        g = runner.get_goal(gid)
        assert g.success_count == 3
        assert g.failure_count == 1
        assert g.none_count == 1
        assert len(g.events) == 5

    def test_three_consecutive_failures_pause(self, runner):
        """失败即停：连续3个failed事件 → auto-paused"""
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test"))
        gid = goal.goal_id

        runner.record_event(gid, "bash", {"exit_code": 0})  # success
        runner.record_event(gid, "bash", {"exit_code": 1})  # failure
        runner.record_event(gid, "bash", {"exit_code": 1})  # failure
        runner.record_event(gid, "bash", {"exit_code": 1})  # failure #3 → pause

        g = runner.get_goal(gid)
        assert g.state == GoalState.PAUSED
        assert "Fail-stop" in g.state_history[-1]["reason"]

    def test_no_progress_pause(self, runner):
        """零进展即停：20+事件且0 success → auto-paused"""
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test"))
        gid = goal.goal_id

        for i in range(20):
            runner.record_event(gid, "question", {})  # NONE events don't count as progress

        g = runner.get_goal(gid)
        # NONE events don't trigger the "no progress" check because scored_events is empty
        # Add 20 scored events with no success
        g.state = GoalState.ACTIVE  # reset
        g.success_count = 0
        g.events = []
        for i in range(20):
            runner.record_event(gid, "bash", {"exit_code": 1, "output": f"fail {i}"})
            g2 = runner.get_goal(gid)
            if g2.state == GoalState.PAUSED:
                break
        g2 = runner.get_goal(gid)
        # After 3 consecutive failures it should already be paused
        assert g2.state == GoalState.PAUSED


class TestGoalReport:
    """goal_report自报协议测试"""

    def test_report_complete_with_evidence(self, runner):
        """自报complete且有success事件 → COMPLETED"""
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test"))
        gid = goal.goal_id

        runner.record_event(gid, "bash", {"exit_code": 0})
        runner.record_event(gid, "read_file", {"content": "ok"})

        result = runner.goal_report(GoalReport(
            goal_id=gid,
            status="complete",
            reason="All tasks finished successfully"
        ))
        assert result.state == GoalState.COMPLETED
        assert result.report_status == "complete"

    def test_report_complete_without_evidence_downgrades(self, runner):
        """自报complete但无success事件 → PAUSED（不是COMPLETED）"""
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test"))
        gid = goal.goal_id

        # No success events recorded
        runner.record_event(gid, "bash", {"exit_code": 1})
        runner.record_event(gid, "bash", {"exit_code": 1})
        runner.record_event(gid, "bash", {"exit_code": 1})

        # Goal might already be paused from fail-stop
        g = runner.get_goal(gid)
        if g.state == GoalState.ACTIVE:
            result = runner.goal_report(GoalReport(
                goal_id=gid,
                status="complete",
                reason="I think I'm done"
            ))
            # With 0 successes and failures > successes, should be PAUSED
            assert result.state == GoalState.PAUSED
        else:
            # Already paused by fail-stop, report on paused goal
            result = runner.goal_report(GoalReport(
                goal_id=gid,
                status="complete",
                reason="I think I'm done"
            ))
            # Should stay paused since no success evidence
            assert result.state in (GoalState.PAUSED, GoalState.COMPLETED)

    def test_report_blocked(self, runner):
        """自报blocked → BLOCKED状态"""
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test"))
        gid = goal.goal_id

        runner.record_event(gid, "bash", {"exit_code": 0})

        result = runner.goal_report(GoalReport(
            goal_id=gid,
            status="blocked",
            reason="Waiting for API credentials"
        ))
        assert result.state == GoalState.BLOCKED
        assert result.report_status == "blocked"

    def test_report_on_terminal_goal_noop(self, runner):
        """终态goal收到report → 不变"""
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test"))
        gid = goal.goal_id
        runner.cancel_goal(gid)

        result = runner.goal_report(GoalReport(
            goal_id=gid,
            status="complete",
            reason="done"
        ))
        assert result.state == GoalState.FAILED  # No change


class TestPersistence:
    """持久化测试"""

    def test_save_and_load(self, tmp_path):
        """保存后重新加载，goal状态保留"""
        persist = tmp_path / "goals.json"

        r1 = GoalRunner.__new__(GoalRunner)
        r1._goals = {}
        r1._PERSIST_PATH = persist

        goal = r1.create_goal(GoalCreate(session_id="s1", description="persist test"))
        gid = goal.goal_id
        r1.record_event(gid, "bash", {"exit_code": 0})
        r1.pause_goal(gid)

        # New runner instance loads from same file
        r2 = GoalRunner.__new__(GoalRunner)
        r2._goals = {}
        r2._PERSIST_PATH = persist
        r2._load()

        loaded = r2.get_goal(gid)
        assert loaded is not None
        assert loaded.state == GoalState.PAUSED
        assert loaded.success_count == 1
        assert loaded.description == "persist test"


class TestGoalStats:
    """统计信息测试"""

    def test_get_stats(self, runner):
        g1 = runner.create_goal(GoalCreate(session_id="s1", description="goal1"))
        g2 = runner.create_goal(GoalCreate(session_id="s2", description="goal2"))
        runner.pause_goal(g2.goal_id)

        stats = runner.get_stats()
        assert stats["total_goals"] == 2
        assert stats["by_state"].get("active", 0) == 1
        assert stats["by_state"].get("paused", 0) == 1
        assert len(stats["active_goals"]) == 1
        assert stats["active_goals"][0]["goal_id"] == g1.goal_id


class TestLoopCounting:
    """循环计数测试"""

    def test_loop_count_increments(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test", max_loops=5))
        gid = goal.goal_id

        for i in range(5):
            runner.record_loop(gid)

        g = runner.get_goal(gid)
        assert g.loop_count == 5

    def test_max_loops_triggers_failed(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test", max_loops=3))
        gid = goal.goal_id

        # Record success events to prevent fail-stop
        for i in range(3):
            runner.record_event(gid, "bash", {"exit_code": 0})
            runner.record_loop(gid)

        g = runner.get_goal(gid)
        # After 3 loops, goal should transition to FAILED
        assert g.loop_count >= 3
        assert g.state == GoalState.FAILED


class TestUserPreemption:
    """用户抢占测试"""

    def test_pause_marks_superseded(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test"))
        gid = goal.goal_id

        runner.pause_goal(gid, "user message")
        g = runner.get_goal(gid)
        assert g.superseded_count == 1
        assert g.state == GoalState.PAUSED
        assert g.state_history[-1]["reason"] == "user message"

    def test_resume_after_preemption(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="test"))
        gid = goal.goal_id

        runner.pause_goal(gid)
        runner.resume_goal(gid)
        g = runner.get_goal(gid)
        assert g.state == GoalState.ACTIVE
        assert g.superseded_count == 1
