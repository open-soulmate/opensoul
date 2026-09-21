"""Workflow engine checkpoint断点续跑测试（STORM分阶段落盘 + agno /continue）。

调研来源：SUMMARY.md §五 will P1 "分阶段checkpoint断点续跑 | STORM分阶段落盘+agno /continue?continue_from"；
40-storm-source.md #17（P0跑到第3阶段崩了不用重跑前2阶段）；65-ag2-source.md #8 checkpoint()/resume_from；
evolution-engine-patterns §5.2 agno"用户触发的一次续跑绝不静默重跑"+retention-exempt；§1.1 mem0失败必须可见。

单元测试无网络（SQLite/JSON全部tmp_path隔离）；live测试经conftest client打:8090。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from src.will.checkpoint import RESUMABLE_STATES, CheckpointStore
from src.will.engine import WorkflowEngine
from src.will.models import (
    ExecutionStatus,
    NodeType,
    StepExecution,
    WorkflowExecution,
)

# ── Helpers ─────────────────────────────────────────────────────


@pytest.fixture
def make_engine(tmp_path, monkeypatch):
    """同名两次调用返回共享同一持久化文件的两个engine实例（模拟进程重启）。"""

    def _make(name: str = "a") -> WorkflowEngine:
        monkeypatch.setattr(WorkflowEngine, "_PERSIST_PATH", tmp_path / f"wf_{name}.json")
        return WorkflowEngine(checkpoint_db=tmp_path / f"ckpt_{name}.db")

    return _make


def _build_pipeline(engine: WorkflowEngine, marker: Path, fail_cmd: str = "mkfs"):
    """trigger → stage1(echo) → stage2(fail_cmd) → stage3(echo) → end"""
    wf = engine.create_workflow(name="ckpt-pipeline")
    n_trigger = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
    n1 = engine.add_node(
        wf.id,
        NodeType.ACTION,
        label="stage1",
        config={"type": "script", "command": f"echo stage1 >> {marker}"},
    )
    n2 = engine.add_node(
        wf.id, NodeType.ACTION, label="stage2", config={"type": "script", "command": fail_cmd}
    )
    n3 = engine.add_node(
        wf.id,
        NodeType.ACTION,
        label="stage3",
        config={"type": "script", "command": f"echo stage3 >> {marker}"},
    )
    n_end = engine.add_node(wf.id, NodeType.END, label="done")
    engine.add_edge(wf.id, n_trigger.id, n1.id)
    engine.add_edge(wf.id, n1.id, n2.id)
    engine.add_edge(wf.id, n2.id, n3.id)
    engine.add_edge(wf.id, n3.id, n_end.id)
    return wf, {"trigger": n_trigger.id, "n1": n1.id, "n2": n2.id, "n3": n3.id}


def _fix_node(engine: WorkflowEngine, wf_id: str, node_id: str, command: str) -> None:
    wf = engine.get_workflow(wf_id)
    assert wf is not None
    for node in wf.nodes:
        if node.id == node_id:
            node.config["command"] = command


def _read_marker(marker: Path) -> list[str]:
    if not marker.exists():
        return []
    return [ln for ln in marker.read_text().splitlines() if ln.strip()]


# ── ① CheckpointStore单元 ──────────────────────────────────────


class TestCheckpointStore:
    def _exec(self, exec_id="exec_1", status=ExecutionStatus.SUCCESS, resume_count=0):
        return WorkflowExecution(
            id=exec_id,
            workflow_id="wf_1",
            workflow_name="test-wf",
            status=status,
            steps=[
                StepExecution(
                    node_id="n1",
                    status=ExecutionStatus.SUCCESS,
                    output_data={"action": "script", "output": "hello"},
                )
            ],
            variables={"action": "script", "output": "hello", "k": "v"},
            trigger_type="manual",
            resume_count=resume_count,
        )

    def test_save_load_roundtrip(self, tmp_path):
        store = CheckpointStore(tmp_path / "ckpt.db")
        store.save(self._exec(resume_count=2))
        loaded = store.load("exec_1")
        assert loaded is not None
        assert loaded.status == ExecutionStatus.SUCCESS
        assert loaded.variables["k"] == "v"
        assert loaded.steps[0].output_data["output"] == "hello"
        assert loaded.resume_count == 2

    def test_load_missing_returns_none(self, tmp_path):
        store = CheckpointStore(tmp_path / "ckpt.db")
        assert store.load("nope") is None

    def test_upsert_single_row_latest_wins(self, tmp_path):
        store = CheckpointStore(tmp_path / "ckpt.db")
        store.save(self._exec())
        updated = self._exec()
        updated.status = ExecutionStatus.FAILED
        updated.error = "node failed"
        updated.steps.append(
            StepExecution(node_id="n2", status=ExecutionStatus.FAILED, error="boom")
        )
        store.save(updated)
        loaded = store.load("exec_1")
        assert loaded.status == ExecutionStatus.FAILED
        assert loaded.error == "node failed"
        assert len(loaded.steps) == 2
        assert store.stats()["total"] == 1  # upsert不产生第二行

    def test_list_resumable_only(self, tmp_path):
        store = CheckpointStore(tmp_path / "ckpt.db")
        store.save(self._exec("exec_ok", ExecutionStatus.SUCCESS))
        store.save(self._exec("exec_fail", ExecutionStatus.FAILED))
        store.save(self._exec("exec_wait", ExecutionStatus.WAITING))
        all_rows = store.list_checkpoints()
        assert {r["execution_id"] for r in all_rows} == {"exec_ok", "exec_fail", "exec_wait"}
        resumable = store.list_checkpoints(resumable_only=True)
        assert {r["execution_id"] for r in resumable} == {"exec_fail", "exec_wait"}

    def test_list_by_workflow(self, tmp_path):
        store = CheckpointStore(tmp_path / "ckpt.db")
        store.save(self._exec("e1"))
        other = self._exec("e2")
        other.workflow_id = "wf_other"
        store.save(other)
        rows = store.list_checkpoints(workflow_id="wf_other")
        assert [r["execution_id"] for r in rows] == ["e2"]

    def test_delete(self, tmp_path):
        store = CheckpointStore(tmp_path / "ckpt.db")
        store.save(self._exec())
        assert store.delete("exec_1") is True
        assert store.load("exec_1") is None
        assert store.delete("exec_1") is False

    def test_prune_exempts_resumable(self, tmp_path):
        """agno retention-exempt：可续跑执行（waiting/failed）豁免清理。"""
        store = CheckpointStore(tmp_path / "ckpt.db")
        for i in range(5):
            e = self._exec(f"ok_{i}", ExecutionStatus.SUCCESS)
            e.started_at = f"2026-01-0{i + 1}T00:00:00+00:00"
            store.save(e)
        store.save(self._exec("fail_keep", ExecutionStatus.FAILED))
        store.save(self._exec("wait_keep", ExecutionStatus.WAITING))
        deleted = store.prune(keep=2)
        assert deleted == 3  # 5条success只留2条
        remaining = {r["execution_id"] for r in store.list_checkpoints(limit=100)}
        assert "fail_keep" in remaining
        assert "wait_keep" in remaining
        assert "ok_4" in remaining and "ok_3" in remaining  # 最近2条success保留

    def test_stats_counts(self, tmp_path):
        store = CheckpointStore(tmp_path / "ckpt.db")
        store.save(self._exec("s1", ExecutionStatus.SUCCESS))
        store.save(self._exec("f1", ExecutionStatus.FAILED))
        store.save(self._exec("w1", ExecutionStatus.WAITING))
        stats = store.stats()
        assert stats["total"] == 3
        assert stats["resumable"] == 2
        assert stats["by_status"]["success"] == 1

    def test_unknown_status_fails_closed_to_waiting(self, tmp_path):
        """未知状态值→WAITING可续跑+日志可见，绝不静默当成功（mem0失败可见）。"""
        db = tmp_path / "ckpt.db"
        store = CheckpointStore(db)
        store.save(self._exec("bogus"))
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE will_checkpoints SET status='bogus_state' WHERE execution_id='bogus'")
        conn.commit()
        conn.close()
        loaded = store.load("bogus")
        assert loaded is not None
        assert loaded.status == ExecutionStatus.WAITING
        assert ExecutionStatus.WAITING in RESUMABLE_STATES

    def test_corrupt_row_skipped_not_fatal(self, tmp_path):
        db = tmp_path / "ckpt.db"
        store = CheckpointStore(db)
        store.save(self._exec("good"))
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE will_checkpoints SET steps='not-json' WHERE execution_id='good'")
        conn.commit()
        conn.close()
        assert store.load("good") is None  # 坏行返回None不抛
        assert store.stats()["total"] == 1  # store整体仍可用


# ── ② 分阶段落盘 + 断点续跑核心行为 ─────────────────────────────


class TestCheckpointResume:
    def test_stage_checkpoint_on_failure(self, make_engine, tmp_path):
        """失败时已完成阶段产物已落盘（STORM分阶段落盘）。"""
        engine = make_engine("core")
        marker = tmp_path / "marker.txt"
        wf, ids = _build_pipeline(engine, marker)

        execution = asyncio.run(engine.execute_async(wf.id))
        assert execution.status == ExecutionStatus.FAILED
        assert "Blocked dangerous command" in (execution.error or "")
        assert _read_marker(marker) == ["stage1"]  # stage1真实执行过

        ckpt = engine._checkpoints.load(execution.id)
        assert ckpt is not None
        assert ckpt.status == ExecutionStatus.FAILED
        by_node = {s.node_id: s for s in ckpt.steps}
        assert by_node[ids["trigger"]].status == ExecutionStatus.SUCCESS
        assert by_node[ids["n1"]].status == ExecutionStatus.SUCCESS
        assert by_node[ids["n2"]].status == ExecutionStatus.FAILED
        # stage1产物在checkpoint variables中（续跑时从盘上恢复，不重新执行）
        # ——script action的result dict（command/exit_code/success）随checkpoint持久化
        assert "stage1" in str(ckpt.variables.get("command", ""))
        assert ckpt.variables.get("exit_code") == 0

    def test_resume_skips_completed_stages(self, make_engine, tmp_path):
        """STORM核心断言：续跑不重跑已完成阶段（跑到第3阶段崩了不用重跑前2阶段）。"""
        engine = make_engine("core")
        marker = tmp_path / "marker.txt"
        wf, ids = _build_pipeline(engine, marker)
        execution = asyncio.run(engine.execute_async(wf.id))
        assert execution.status == ExecutionStatus.FAILED
        n1_completed_at = next(s.completed_at for s in execution.steps if s.node_id == ids["n1"])

        # "修复"失败节点后续跑
        _fix_node(engine, wf.id, ids["n2"], f"echo stage2 >> {marker}")
        resumed, reason = asyncio.run(engine.resume_execution(execution.id))
        assert resumed is not None
        assert resumed.status == ExecutionStatus.SUCCESS
        assert reason.startswith("resumed:")
        assert "replayed from checkpoint" in reason

        lines = _read_marker(marker)
        # stage1只出现一次=没有重新执行；stage2/stage3续跑时执行
        assert lines == ["stage1", "stage2", "stage3"]
        assert resumed.resume_count == 1
        # checkpoint中stage1的completed_at原样保留（时间戳来自首次执行）
        n1_step = next(s for s in resumed.steps if s.node_id == ids["n1"])
        assert n1_step.completed_at == n1_completed_at

    def test_resume_refuses_success_and_unknown(self, make_engine, tmp_path):
        """agno"绝不静默重跑"：SUCCESS执行拒绝续跑；未知id显式not_found。"""
        engine = make_engine("core")
        marker = tmp_path / "marker.txt"
        wf, ids = _build_pipeline(engine, marker)
        execution = asyncio.run(engine.execute_async(wf.id))
        _fix_node(engine, wf.id, ids["n2"], f"echo stage2 >> {marker}")
        resumed, _ = asyncio.run(engine.resume_execution(execution.id))
        assert resumed.status == ExecutionStatus.SUCCESS

        again, reason = asyncio.run(engine.resume_execution(execution.id))
        assert again is None
        assert "not_resumable" in reason
        assert "success" in reason

        missing, reason = asyncio.run(engine.resume_execution("exec_does_not_exist"))
        assert missing is None
        assert reason == "not_found"

    def test_resume_budget_grant_exactly_once(self, make_engine, tmp_path):
        """每次resume恰好一遍（budget grant），失败再次可见，resume_count逐次+1。"""
        engine = make_engine("core")
        marker = tmp_path / "marker.txt"
        wf, ids = _build_pipeline(engine, marker)
        execution = asyncio.run(engine.execute_async(wf.id))
        assert execution.status == ExecutionStatus.FAILED

        # 不修复节点直接续跑：再失败，错误原文可见，stage1仍不重跑
        r1, _ = asyncio.run(engine.resume_execution(execution.id))
        assert r1.status == ExecutionStatus.FAILED
        assert "Blocked dangerous command" in (r1.error or "")
        assert r1.resume_count == 1
        assert _read_marker(marker) == ["stage1"]

        r2, _ = asyncio.run(engine.resume_execution(execution.id))
        assert r2.status == ExecutionStatus.FAILED
        assert r2.resume_count == 2  # 每次人工触发恰好+1，不静默追加

    def test_resume_condition_edge_recomputed(self, make_engine, tmp_path):
        """条件边在续跑frontier用checkpoint变量重新求值——未选中分支绝不误执行。"""
        engine = make_engine("cond")
        marker_a = tmp_path / "branch_a.txt"
        marker_b = tmp_path / "branch_b.txt"
        wf = engine.create_workflow(name="cond-wf")
        n_trigger = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
        n_cond = engine.add_node(
            wf.id, NodeType.CONDITION, label="check", config={"expression": "go == 1"}
        )
        n_a = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="branch-a",
            config={"type": "script", "command": f"echo A >> {marker_a}"},
        )
        n_fail = engine.add_node(
            wf.id, NodeType.ACTION, label="a-fail", config={"type": "script", "command": "mkfs"}
        )
        n_b = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="branch-b",
            config={"type": "script", "command": f"echo B >> {marker_b}"},
        )
        engine.add_edge(wf.id, n_trigger.id, n_cond.id)
        engine.add_edge(wf.id, n_cond.id, n_a.id, condition="condition_result == True")
        engine.add_edge(wf.id, n_cond.id, n_b.id, condition="condition_result == False")
        engine.add_edge(wf.id, n_a.id, n_fail.id)

        execution = asyncio.run(engine.execute_async(wf.id, input_vars={"go": 1}))
        assert execution.status == ExecutionStatus.FAILED
        assert _read_marker(marker_a) == ["A"]
        assert _read_marker(marker_b) == []  # go=1时B分支未被选中

        _fix_node(engine, wf.id, n_fail.id, "echo fixed")
        resumed, _ = asyncio.run(engine.resume_execution(execution.id))
        assert resumed.status == ExecutionStatus.SUCCESS
        # 续跑后：A未重跑（仍1行），B仍未执行（checkpoint变量求值condition_result仍True）
        assert _read_marker(marker_a) == ["A"]
        assert _read_marker(marker_b) == []

    def test_resume_refused_when_workflow_missing(self, make_engine, tmp_path):
        engine = make_engine("gone")
        marker = tmp_path / "marker.txt"
        wf, ids = _build_pipeline(engine, marker)
        execution = asyncio.run(engine.execute_async(wf.id))
        assert engine.delete_workflow(wf.id) is True
        resumed, reason = asyncio.run(engine.resume_execution(execution.id))
        assert resumed is None
        assert "workflow_missing" in reason
        # error可见性：checkpoint行带原因
        ckpt = engine._checkpoints.load(execution.id)
        assert "workflow_missing" in (ckpt.error or "")


# ── ③ 进程重启恢复（Langflow按run_id恢复 + mem0失败可见） ──────


class TestRestartRecovery:
    def test_interrupted_running_marked_waiting(self, make_engine, tmp_path):
        """RUNNING执行的进程死了→重启恢复为WAITING+interrupted原因（显式可见）。"""
        store = CheckpointStore(tmp_path / "ckpt_r.db")
        crashed = WorkflowExecution(
            id="exec_crash",
            workflow_id="wf_ghost",
            status=ExecutionStatus.RUNNING,
            steps=[
                StepExecution(node_id="n1", status=ExecutionStatus.SUCCESS, output_data={"done": 1})
            ],
        )
        store.save(crashed)

        engine = make_engine("r")  # 新进程，同一checkpoint db
        recovered = engine.get_execution("exec_crash")
        assert recovered is not None
        assert recovered.status == ExecutionStatus.WAITING
        assert "interrupted" in recovered.error
        assert "/api/will/executions/exec_crash/continue" in recovered.error
        assert recovered.steps[0].status == ExecutionStatus.SUCCESS  # 已完成阶段产物保留

    def test_terminal_history_survives_restart(self, make_engine, tmp_path):
        """重启不丢失执行历史（可观测性）+ 续跑在新进程上从盘恢复后成功。"""
        engine_a = make_engine("restart")
        marker = tmp_path / "marker.txt"
        wf, ids = _build_pipeline(engine_a, marker)
        exec_a = asyncio.run(engine_a.execute_async(wf.id))
        assert exec_a.status == ExecutionStatus.FAILED

        # 模拟进程重启：全新engine实例，同一持久化文件
        engine_b = make_engine("restart")
        recovered = engine_b.get_execution(exec_a.id)
        assert recovered is not None
        assert recovered.status == ExecutionStatus.FAILED  # 历史可见，未丢失
        assert engine_b.list_executions(workflow_id=wf.id)  # 列表读路径也恢复

        # 新进程上修复节点并续跑——STORM"从磁盘恢复上一阶段产物"
        _fix_node(engine_b, wf.id, ids["n2"], f"echo stage2 >> {marker}")
        resumed, _ = asyncio.run(engine_b.resume_execution(exec_a.id))
        assert resumed.status == ExecutionStatus.SUCCESS
        assert _read_marker(marker) == ["stage1", "stage2", "stage3"]

    def test_get_execution_falls_back_to_store(self, make_engine, tmp_path):
        """内存trim后按id仍可从checkpoint store读回（Langflow按run_id恢复读路径）。"""
        engine = make_engine("fallback")
        marker = tmp_path / "marker.txt"
        wf, _ = _build_pipeline(engine, marker)
        execution = asyncio.run(engine.execute_async(wf.id))
        engine._executions.pop(execution.id)  # 模拟_trim_history裁掉内存记录
        recovered = engine.get_execution(execution.id)
        assert recovered is not None
        assert recovered.id == execution.id
        assert recovered.status == ExecutionStatus.FAILED

    def test_stats_include_checkpoint_observability(self, make_engine, tmp_path):
        engine = make_engine("stats")
        marker = tmp_path / "marker.txt"
        wf, _ = _build_pipeline(engine, marker)
        asyncio.run(engine.execute_async(wf.id))
        stats = engine.stats()
        assert "checkpoints" in stats
        assert stats["checkpoints"]["total"] >= 1
        assert stats["checkpoints"]["resumable"] >= 1  # failed可续跑
        assert "waiting_resume" in stats


# ── ④ live API（:8090真实服务，conftest client；重启服务后运行） ──


class TestLiveCheckpointAPI:
    """live测试在重启后的opensoul服务上运行；测试产物经DELETE端点清理。"""

    @pytest.mark.live
    def test_checkpoint_api_full_cycle(self, client):
        # ① 创建三阶段工作流（stage2用被拦截命令制造失败现场）
        resp = client.post(
            "/api/will/workflows", json={"name": "cron-ckpt-live", "description": "ckpt e2e"}
        )
        assert resp.status_code in (200, 201)
        wf_id = resp.json().get("workflow_id") or resp.json().get("id")
        assert wf_id
        created_nodes = []

        def _node(node_type, label, config=None):
            r = client.post(
                f"/api/will/workflows/{wf_id}/nodes",
                json={"node_type": node_type, "label": label, "config": config or {}},
            )
            assert r.status_code == 200
            node_id = r.json()["id"]
            created_nodes.append(node_id)
            return node_id

        n_trigger = _node("trigger", "start")
        n1 = _node("action", "stage1", {"type": "script", "command": "echo live-stage1"})
        n2 = _node("action", "stage2", {"type": "script", "command": "mkfs"})
        n3 = _node("action", "stage3", {"type": "script", "command": "echo live-stage3"})
        for src, dst in [(n_trigger, n1), (n1, n2), (n2, n3)]:
            r = client.post(
                f"/api/will/workflows/{wf_id}/edges",
                json={"source_node_id": src, "target_node_id": dst},
            )
            assert r.status_code == 200

        try:
            # ② 执行→stage2被拦截→FAILED，checkpoint落盘
            resp = client.post(f"/api/will/workflows/{wf_id}/execute", json={})
            assert resp.status_code == 200
            body = resp.json()
            exec_id = body["id"]
            assert body["status"] == "failed"
            assert "Blocked dangerous command" in (body.get("error") or "")
            assert body["resume_count"] == 0
            step_statuses = {s["node_label"]: s["status"] for s in body["steps"]}
            assert step_statuses.get("stage1") == "success"
            assert step_statuses.get("stage2") == "failed"

            # ③ GET execution携带resume_count（新观测字段live在响应中）
            resp = client.get(f"/api/will/executions/{exec_id}")
            assert resp.status_code == 200
            assert resp.json()["resume_count"] == 0

            # ④ /checkpoints端点：该执行在列且resumable
            resp = client.get("/api/will/checkpoints", params={"workflow_id": wf_id})
            assert resp.status_code == 200
            rows = resp.json()["checkpoints"]
            hit = [r for r in rows if r["execution_id"] == exec_id]
            assert hit, f"checkpoint for {exec_id} not in {rows}"
            assert hit[0]["status"] == "failed"
            resp = client.get(
                "/api/will/checkpoints", params={"workflow_id": wf_id, "resumable_only": True}
            )
            assert exec_id in [r["execution_id"] for r in resp.json()["checkpoints"]]

            # ⑤ /continue：failed可续跑（节点未修复→再失败一次，错误可见，resume_count=1）
            resp = client.post(f"/api/will/executions/{exec_id}/continue")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "failed"
            assert body["resume_count"] == 1
            assert body["resume"].startswith("resumed:")
            # 续跑不重跑stage1：steps中stage1的completed_at来自checkpoint（无新增stage1失败步）
            stage1_steps = [s for s in body["steps"] if s["node_label"] == "stage1"]
            assert len(stage1_steps) == 1 and stage1_steps[0]["status"] == "success"

            # ⑥ SUCCESS执行拒绝续跑（409绝不静默重跑）：用健康工作流验证
            resp2 = client.post(
                "/api/will/workflows", json={"name": "cron-ckpt-live-ok", "description": ""}
            )
            wf2_id = resp2.json().get("workflow_id") or resp2.json().get("id")
            # 为wf2建健康两节点工作流（trigger→script echo ok）
            r = client.post(
                f"/api/will/workflows/{wf2_id}/nodes",
                json={"node_type": "trigger", "label": "t", "config": {}},
            )
            t2_id = r.json()["id"]
            r = client.post(
                f"/api/will/workflows/{wf2_id}/nodes",
                json={
                    "node_type": "action",
                    "label": "ok",
                    "config": {"type": "script", "command": "echo ok"},
                },
            )
            ok_id = r.json()["id"]
            client.post(
                f"/api/will/workflows/{wf2_id}/edges",
                json={"source_node_id": t2_id, "target_node_id": ok_id},
            )
            resp = client.post(f"/api/will/workflows/{wf2_id}/execute", json={})
            ok_exec = resp.json()
            assert ok_exec["status"] == "success"
            resp = client.post(f"/api/will/executions/{ok_exec['id']}/continue")
            assert resp.status_code == 409
            assert "not_resumable" in resp.json()["detail"]

            # ⑦ /stats携带checkpoints观测键
            resp = client.get("/api/will/stats")
            assert resp.status_code == 200
            assert "checkpoints" in resp.json()

            # ⑧ 未知id → 404
            resp = client.post("/api/will/executions/exec_nonexistent/continue")
            assert resp.status_code == 404
            assert "not_found" in resp.json()["detail"]
        finally:
            # 清理：checkpoint行+工作流（DELETE端点；生产数据零残留）
            for r in client.get("/api/will/checkpoints", params={"limit": 500}).json()[
                "checkpoints"
            ]:
                if r["workflow_id"] in (wf_id, locals().get("wf2_id")):
                    client.delete(f"/api/will/checkpoints/{r['execution_id']}")
            client.delete(f"/api/will/workflows/{wf_id}")
            if "wf2_id" in locals():
                client.delete(f"/api/will/workflows/{wf2_id}")
