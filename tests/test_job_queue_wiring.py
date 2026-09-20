"""P0-8 后台作业队列运行时接线测试 — handler注册/懒启动/stale回收/幂等键/生产者全链路。

调研来源对照：
- agno job_queue/store.py（evolution-engine-patterns.md §5.2）：idempotency_key去重、
  stale锁回收（单进程部署差异已在job_queue.py docstring注明）
- mem0 §1.1 "失败必须可见"：未注册handler→FAILED带error，不静默
- 本轮修复的缺口：c1feb676队列基础设施零接线（register_handler/start/submit调用点全为0）

单元测试无网络依赖：SQLite用tmp_path隔离；live测试经conftest client → localhost:8090。
"""

import asyncio
import sqlite3
import time

import pytest

from src.will.job_handlers import HANDLER_SPECS, register_default_handlers
from src.will.job_queue import JobQueue, JobStatus


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def jq(tmp_path):
    return JobQueue(db_path=str(tmp_path / "jobs.db"), max_workers=2)


async def _poll(q: JobQueue, job_id: str, timeout: float = 8.0) -> dict:
    """轮询作业状态直到终态（live/单元共用语义）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = q.get(job_id)
        if info and info["status"] in ("completed", "failed", "timeout", "cancelled"):
            return info
        await asyncio.sleep(0.05)
    return q.get(job_id) or {}


# ── ① handler注册 ────────────────────────────────────────────


class TestHandlerRegistration:
    def test_default_specs_contain_real_organ_handlers(self):
        # handler命名=<organ>.<action>，指向真实器官函数而非日志占位
        assert "heredity.evaluate_triggers" in HANDLER_SPECS
        assert "hippo.dream" in HANDLER_SPECS

    def test_register_idempotent(self, jq):
        first = register_default_handlers(jq)
        second = register_default_handlers(jq)
        assert first == second
        assert sorted(jq._handlers.keys()) == sorted(HANDLER_SPECS.keys())

    def test_unknown_handler_fails_visibly(self, jq):
        async def scenario():
            job_id = await jq.submit("no.such.handler", {})
            await jq.start()
            info = await _poll(jq, job_id)
            await jq.stop()
            return info

        info = run(scenario())
        assert info["status"] == "failed"
        assert "No handler for job type" in info["error"]


# ── ② 执行管线 ───────────────────────────────────────────────


class TestExecution:
    def test_submit_executes_handler_to_completion(self, jq):
        async def scenario():
            async def echo(**params):
                return {"echo": params.get("x")}

            jq.register_handler("t.echo", echo)
            await jq.start()
            job_id = await jq.submit("t.echo", {"x": 42})
            info = await _poll(jq, job_id)
            await jq.stop()
            return job_id, info

        job_id, info = run(scenario())
        assert info["status"] == "completed"
        assert info["result"] == {"echo": 42}
        assert info["started_at"] > 0 and info["finished_at"] >= info["started_at"]

    def test_result_survives_process_restart(self, jq, tmp_path):
        """DB为真源：模拟进程重启（新实例同db_path）后历史与结果仍可读"""

        async def scenario():
            async def echo(**params):
                return {"v": params.get("v")}

            jq.register_handler("t.echo", echo)
            await jq.start()
            job_id = await jq.submit("t.echo", {"v": "persisted"})
            info = await _poll(jq, job_id)
            await jq.stop()
            return job_id, info

        job_id, info = run(scenario())
        assert info["status"] == "completed"

        jq2 = JobQueue(db_path=str(tmp_path / "jobs.db"), max_workers=1)
        revived = jq2.get(job_id)
        assert revived is not None
        assert revived["status"] == "completed"
        assert revived["result"] == {"v": "persisted"}
        listed = jq2.list_jobs()
        assert any(j["id"] == job_id for j in listed)
        stats = jq2.get_stats()
        assert stats["by_status"].get("completed", 0) >= 1

    def test_retry_then_succeed(self, jq):
        async def scenario():
            calls = {"n": 0}

            async def flaky(**params):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("transient blip")
                return {"attempts": calls["n"]}

            jq.register_handler("t.flaky", flaky)
            await jq.start()
            job_id = await jq.submit("t.flaky", {}, max_retries=2)
            info = await _poll(jq, job_id)
            await jq.stop()
            return info

        info = run(scenario())
        assert info["status"] == "completed"
        assert info["retries"] == 1
        assert info["result"] == {"attempts": 2}

    def test_retries_exhausted_fails_with_error(self, jq):
        async def scenario():
            async def broken(**params):
                raise ValueError("always fails")

            jq.register_handler("t.broken", broken)
            await jq.start()
            job_id = await jq.submit("t.broken", {}, max_retries=1)
            info = await _poll(jq, job_id)
            await jq.stop()
            return info

        info = run(scenario())
        assert info["status"] == "failed"
        assert "always fails" in info["error"]
        assert info["retries"] == 1  # 重试计数可见，失败不静默（mem0 §1.1）


# ── ③ stale回收（agno stale回收的单进程版） ──────────────────


class TestStaleRecovery:
    def test_stale_running_requeued_on_start(self, jq, tmp_path):
        """死进程遗留的running作业：新进程start()→回pending重排队→执行完成"""
        # 直接往DB写一条"上个进程死在running"的作业
        with sqlite3.connect(str(tmp_path / "jobs.db")) as conn:
            conn.execute(
                "INSERT INTO jobs (id, name, status, params, created_at, retries, max_retries, idempotency_key) "
                "VALUES ('job_stale001', 't.rec', 'running', '{}', ?, 0, 2, '')",
                (time.time(),),
            )

        async def scenario():
            async def rec(**params):
                return {"recovered": True}

            jq.register_handler("t.rec", rec)
            await jq.start()
            info = await _poll(jq, "job_stale001")
            await jq.stop()
            return info

        info = run(scenario())
        assert info["status"] == "completed"
        assert info["result"] == {"recovered": True}

    def test_pending_from_db_requeued(self, jq, tmp_path):
        with sqlite3.connect(str(tmp_path / "jobs.db")) as conn:
            conn.execute(
                "INSERT INTO jobs (id, name, status, params, created_at, retries, max_retries, idempotency_key) "
                "VALUES ('job_pend001', 't.rec2', 'pending', '{}', ?, 0, 2, '')",
                (time.time(),),
            )

        async def scenario():
            async def rec2(**params):
                return {"ok": 1}

            jq.register_handler("t.rec2", rec2)
            await jq.start()
            info = await _poll(jq, "job_pend001")
            await jq.stop()
            return info

        info = run(scenario())
        assert info["status"] == "completed"


# ── ④ idempotency_key（agno §5.2幂等键） ─────────────────────


class TestIdempotency:
    def test_same_key_pending_dedupes(self, jq):
        async def scenario():
            # 不start()：作业停留pending，两次提交同key
            jid1 = await jq.submit("t.x", {"n": 1}, idempotency_key="key-A")
            jid2 = await jq.submit("t.x", {"n": 2}, idempotency_key="key-A")
            return jid1, jid2

        jid1, jid2 = run(scenario())
        assert jid1 == jid2
        rows = jq.list_jobs()
        assert len([j for j in rows if j.get("status") == "pending"]) == 1
        # 只有第一次的params被保留
        assert rows[0]["params"] == {"n": 1}

    def test_completed_key_does_not_block_resubmit(self, jq):
        """已完成的同key作业不拦新提交（重跑是合法意图）"""

        async def scenario():
            async def quick(**params):
                return {"n": params.get("n")}

            jq.register_handler("t.quick", quick)
            await jq.start()
            jid1 = await jq.submit("t.quick", {"n": 1}, idempotency_key="key-B")
            await _poll(jq, jid1)
            jid2 = await jq.submit("t.quick", {"n": 2}, idempotency_key="key-B")
            info2 = await _poll(jq, jid2)
            await jq.stop()
            return jid1, jid2, info2

        jid1, jid2, info2 = run(scenario())
        assert jid1 != jid2
        assert info2["status"] == "completed"
        assert info2["result"] == {"n": 2}

    def test_find_by_idempotency_key(self, jq):
        async def scenario():
            jid = await jq.submit("t.x", {}, idempotency_key="key-C")
            found = jq.find_by_idempotency_key("key-C")
            return jid, found

        jid, found = run(scenario())
        assert found == jid
        assert jq.find_by_idempotency_key("nope") is None
        assert jq.find_by_idempotency_key("") is None

    def test_key_persisted_in_db(self, jq):
        run(jq.submit("t.x", {}, idempotency_key="key-D"))
        with sqlite3.connect(jq._db_path) as conn:
            row = conn.execute(
                "SELECT idempotency_key FROM jobs WHERE idempotency_key='key-D'"
            ).fetchone()
        assert row is not None


# ── ⑤ live API（:8090真实服务，conftest client） ─────────────


@pytest.mark.live
class TestLiveJobQueueAPI:
    """对live opensoul服务验证端到端接线（重启服务后运行）。"""

    def test_health_lists_registered_handlers(self, client):
        r = client.get("/api/will/jobs/health")
        assert r.status_code == 200
        data = r.json()
        assert data["component"] == "JobQueue"
        for name in ("heredity.evaluate_triggers", "hippo.dream"):
            assert name in data["handlers"], f"handler {name} 未注册=未接线"

    def test_submit_evaluate_triggers_job_completes(self, client):
        r = client.post(
            "/api/will/jobs/submit",
            json={"name": "heredity.evaluate_triggers", "params": {"idle_seconds": 0}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "submitted"
        assert body["workers"] >= 1, "懒启动后worker池必须存在"
        job_id = body["job_id"]

        deadline = time.time() + 15
        info = None
        while time.time() < deadline:
            gr = client.get(f"/api/will/jobs/{job_id}")
            assert gr.status_code == 200
            info = gr.json()
            if info["status"] in ("completed", "failed", "timeout"):
                break
            time.sleep(0.3)
        assert info is not None and info["status"] == "completed", f"job未完成: {info}"
        assert isinstance(info["result"], dict)
        # evaluate_triggers真实返回（fired/breaker_open/suppressed之一必存在）
        assert any(k in info["result"] for k in ("fired", "breaker_open", "suppressed")), info[
            "result"
        ]

    def test_dream_background_producer(self, client):
        r = client.post("/api/hippo/ltm/dream", json={"messages": [], "background": True})
        assert r.status_code == 200
        body = r.json()
        assert body["queued"] is True
        assert body["handler"] == "hippo.dream"
        job_id = body["job_id"]

        deadline = time.time() + 15
        info = None
        while time.time() < deadline:
            gr = client.get(f"/api/will/jobs/{job_id}")
            assert gr.status_code == 200
            info = gr.json()
            if info["status"] in ("completed", "failed", "timeout"):
                break
            time.sleep(0.3)
        assert info is not None and info["status"] == "completed", f"job未完成: {info}"
        # dream空消息路径：不烧LLM，返回"No messages to distill"（诚实结果）
        assert info["result"]["error"] == "No messages to distill"
        assert info["result"]["dream_id"].startswith("dream_")

    def test_dream_sync_path_unchanged(self, client):
        """background=False保持既有同步行为（增量改动不破坏原路径）"""
        r = client.post("/api/hippo/ltm/dream", json={"messages": []})
        assert r.status_code == 200
        body = r.json()
        assert "queued" not in body
        assert body["dream_id"].startswith("dream_")

    def test_idempotency_live(self, client):
        key = f"itest-{int(time.time())}"
        r1 = client.post(
            "/api/will/jobs/submit",
            json={
                "name": "heredity.evaluate_triggers",
                "params": {"idle_seconds": 0},
                "idempotency_key": key,
                "timeout_s": 1,
            },
        )
        jid1 = r1.json()["job_id"]
        r2 = client.post(
            "/api/will/jobs/submit",
            json={
                "name": "heredity.evaluate_triggers",
                "params": {"idle_seconds": 0},
                "idempotency_key": key,
                "timeout_s": 1,
            },
        )
        body2 = r2.json()
        # 第二次提交可能撞上pending/running窗口（deduped）或已完成（新作业），
        # 两者都是合法语义；此断言只允许这两种情况，不允许空id
        assert body2["job_id"]
        if body2.get("deduped"):
            assert body2["job_id"] == jid1

    def test_jobs_list_shows_history(self, client):
        r = client.get("/api/will/jobs?limit=20")
        assert r.status_code == 200
        jobs = r.json()["jobs"]
        assert isinstance(jobs, list)
        # 前面的live用例已产生作业
        assert len(jobs) >= 1
        names = {j["name"] for j in jobs}
        assert "heredity.evaluate_triggers" in names or "hippo.dream" in names
