"""codex两阶段记忆管线测试（11-openai-codex-source.md #1）。

覆盖：MemoryVersion版本化（ADD/UPDATE/MERGE版本、回滚、失败可见）、
Phase1/Phase2宽容解析、管线端到端（LLM路径/import直供/降级/dry-run）、
workspace diff、prune旧资源修剪、pipeline_runs持久化、job handler接线。
无live server依赖：tmp_path SQLite + FakeLLM。
"""
import asyncio
import json
import time

import pytest

from src.hippo.long_term_memory import LongTermMemoryStore
from src.hippo.memory_pipeline import (
    ConsolidationDecision,
    MemoryPipeline,
    Phase1Candidate,
    parse_phase1,
    parse_phase2,
)
from src.will.job_handlers import HANDLER_SPECS, register_default_handlers


def run(coro):
    return asyncio.run(coro)


def make_llm(response_text):
    """Fake LLM：原样返回response_text（str或OpenAI风格dict——解包回归测试用）。"""
    async def _llm(system_prompt, user_prompt):
        return response_text
    return _llm


class FailingLLM:
    async def __call__(self, system_prompt, user_prompt):
        raise RuntimeError("provider down")


@pytest.fixture
def store(tmp_path):
    return LongTermMemoryStore(
        db_path=str(tmp_path / "ltm.db"), gatekeeper_enabled=True
    )


@pytest.fixture
def pipeline(store):
    return MemoryPipeline(ltm_store=store)


# ── MemoryVersion版本化 ──────────────────────────────────────────

class TestMemoryVersion:
    def test_store_writes_v1(self, store):
        mem = store.store(content="用户偏好深色主题", memory_type="semantic")
        versions = store.get_versions(mem.memory_id)
        assert len(versions) == 1
        assert versions[0]["version"] == 1
        assert versions[0]["event"] == "ADD"
        assert versions[0]["content"] == "用户偏好深色主题"

    def test_update_writes_v2(self, store):
        mem = store.store(content="第一条内容", memory_type="semantic")
        store.update_memory(memory_id=mem.memory_id, content="第一条内容修订版")
        versions = store.get_versions(mem.memory_id)
        assert len(versions) == 2
        assert versions[0]["version"] == 2
        assert versions[0]["event"] == "UPDATE"
        assert versions[0]["content"] == "第一条内容修订版"
        assert versions[1]["content"] == "第一条内容"  # v1快照保留

    def test_current_version_progression(self, store):
        mem = store.store(content="版本演进测试事实", memory_type="semantic")
        assert store.get_current_version(mem.memory_id) == 1
        store.update_memory(memory_id=mem.memory_id, content="版本演进测试事实改")
        assert store.get_current_version(mem.memory_id) == 2

    def test_rollback_restores_and_writes_new_version(self, store):
        mem = store.store(content="原始内容ABC", memory_type="semantic", importance=0.6)
        store.update_memory(memory_id=mem.memory_id, content="被改坏的内容XYZ", importance=0.9)
        result = store.rollback_version(mem.memory_id, version=1)
        assert result is not None
        assert result["content"] == "原始内容ABC"
        assert float(result["importance"]) == pytest.approx(0.6)
        # 回滚本身写v3（历史链只增不改）
        assert store.get_current_version(mem.memory_id) == 3
        versions = store.get_versions(mem.memory_id)
        assert versions[0]["version"] == 3
        assert "rollback_to_v1" in versions[0]["reason"]

    def test_rollback_unknown_version_returns_none(self, store):
        mem = store.store(content="回滚目标缺失测试", memory_type="semantic")
        assert store.rollback_version(mem.memory_id, version=99) is None

    def test_rollback_unknown_memory_returns_none(self, store):
        assert store.rollback_version("ltm_nonexistent", version=1) is None

    def test_merge_writes_merge_version(self, store):
        mem = store.store(content="完全相同的事实条目", memory_type="semantic", importance=0.5)
        # 同内容再写dup_policy=merge→fact_dedup并入→MERGE版本事件
        merged = store.store(
            content="完全相同的事实条目", memory_type="semantic",
            importance=0.8, dup_policy="merge",
        )
        assert merged is not None
        assert merged.memory_id == mem.memory_id
        versions = store.get_versions(mem.memory_id)
        events = [v["event"] for v in versions]
        assert "MERGE" in events
        merge_ver = next(v for v in versions if v["event"] == "MERGE")
        assert float(merge_ver["importance"]) == pytest.approx(0.8)  # importance取max

    def test_versions_parsed_types(self, store):
        mem = store.store(content="类型解析测试", memory_type="semantic", tags=["a", "b"])
        versions = store.get_versions(mem.memory_id)
        assert versions[0]["tags"] == ["a", "b"]
        assert isinstance(versions[0]["metadata"], dict)

    def test_legacy_memory_no_versions(self, store):
        assert store.get_current_version("ltm_pre_deploy") == 0
        assert store.get_versions("ltm_pre_deploy") == []


# ── Phase1解析 ───────────────────────────────────────────────────

class TestPhase1Parse:
    def test_valid_json(self):
        resp = json.dumps([
            {"content": "用户使用Arch Linux", "memory_type": "semantic",
             "importance": 0.7, "tags": ["os"], "scope": "user",
             "durability": "durable", "authority": "descriptive",
             "evidence": "我用的是Arch", "reason": "环境事实"},
        ], ensure_ascii=False)
        cands = parse_phase1(resp)
        assert len(cands) == 1
        assert cands[0].content == "用户使用Arch Linux"
        assert cands[0].scope == "user"
        assert cands[0].evidence == "我用的是Arch"

    def test_fenced_json(self):
        resp = '分析如下：\n```json\n[{"content": "事实A", "importance": 0.6}]\n```'
        cands = parse_phase1(resp)
        assert len(cands) == 1
        assert cands[0].content == "事实A"

    def test_invalid_json_returns_empty(self):
        assert parse_phase1("这不是JSON") == []
        assert parse_phase1("```json\n{broken\n```") == []

    def test_non_array_returns_empty(self):
        assert parse_phase1('{"content": "x"}') == []

    def test_missing_content_filtered(self):
        resp = '[{"importance": 0.5}, {"content": "  "}, {"content": "有效"}]'
        cands = parse_phase1(resp)
        assert len(cands) == 1
        assert cands[0].content == "有效"

    def test_importance_clamp_and_type_fallback(self):
        resp = json.dumps([
            {"content": "高值", "importance": 99},
            {"content": "坏类型", "memory_type": "banana"},
            {"content": "坏重要度", "importance": "not-a-number"},
        ])
        cands = parse_phase1(resp)
        assert cands[0].importance == 1.0
        assert cands[1].memory_type == "semantic"
        assert cands[2].importance == 0.5


# ── Phase2解析 ───────────────────────────────────────────────────

def _cands(n=4):
    return [Phase1Candidate(content=f"候选{i}", importance=0.6) for i in range(n)]


class TestPhase2Parse:
    def test_valid_decisions_all_ops(self):
        resp = json.dumps([
            {"candidate_index": 0, "op": "ADD_NEW", "reason": "新事实"},
            {"candidate_index": 1, "op": "MERGE_INTO", "target_memory_id": "ltm_a",
             "merged_content": "合并后", "reason": "重复"},
            {"candidate_index": 2, "op": "UPDATE", "target_memory_id": "ltm_b",
             "merged_content": "更新后", "reason": "过时"},
            {"candidate_index": 3, "op": "REJECT", "reason": "低价值"},
        ])
        decisions, meta = parse_phase2(resp, _cands())
        assert [d.op for d in decisions] == ["ADD_NEW", "MERGE_INTO", "UPDATE", "REJECT"]
        assert decisions[1].target_memory_id == "ltm_a"
        assert decisions[1].merged_content == "合并后"
        assert meta["skipped_out_of_range"] == 0

    def test_merge_missing_target_falls_back_to_add_new(self):
        resp = '[{"candidate_index": 0, "op": "MERGE_INTO", "reason": "缺目标"}]'
        decisions, meta = parse_phase2(resp, _cands())
        assert decisions[0].op == "ADD_NEW"
        assert "[fallback:missing_target]" in decisions[0].reason
        assert meta["missing_target_fallback"] == 1

    def test_out_of_range_index_skipped(self):
        resp = json.dumps([
            {"candidate_index": 0, "op": "ADD_NEW"},
            {"candidate_index": 99, "op": "ADD_NEW"},
        ])
        decisions, meta = parse_phase2(resp, _cands())
        assert len(decisions) == 1
        assert meta["skipped_out_of_range"] == 1

    def test_invalid_op_skipped(self):
        resp = json.dumps([
            {"candidate_index": 0, "op": "TELEPORT"},
            {"candidate_index": 1, "op": "add_new"},  # 小写宽容
        ])
        decisions, meta = parse_phase2(resp, _cands())
        assert len(decisions) == 1
        assert decisions[0].op == "ADD_NEW"
        assert meta["skipped_invalid_op"] == 1

    def test_empty_and_unparseable(self):
        assert parse_phase2("", _cands()) == ([], parse_phase2("", _cands())[1])
        decisions, _ = parse_phase2("没有JSON", _cands())
        assert decisions == []
        decisions, _ = parse_phase2('{"op": "ADD_NEW"}', _cands())
        assert decisions == []


# ── 管线端到端 ───────────────────────────────────────────────────

PHASE1_RESP = json.dumps([
    {"content": "用户偏好用中文回复", "memory_type": "semantic", "importance": 0.8,
     "tags": ["preference"], "scope": "user", "durability": "durable",
     "authority": "descriptive", "evidence": "以后请用中文回复", "reason": "用户明确要求"},
    {"content": "项目用Python 3.12", "memory_type": "semantic", "importance": 0.6,
     "scope": "user", "durability": "durable", "authority": "descriptive"},
], ensure_ascii=False)


class TestPipelineRun:
    def test_full_llm_run_add_new(self, store):
        llm = MemoryPipeline(
            ltm_store=store,
            llm_call=make_llm(PHASE1_RESP),  # Phase1响应；Phase2同样返回（解析为空→降级）
        )
        result = run(llm.run(
            messages=[{"role": "user", "content": "以后请用中文回复，项目用Python 3.12"}],
            session_id="sess-1",
        ))
        assert result.mode == "llm"
        assert result.phase1_count == 2
        assert result.error == ""
        # Phase2拿到Phase1输出格式→解析不出决策→确定性降级（fallback可见）
        assert result.phase2_meta["phase2"] == "deterministic"
        assert "phase2_fallback_reason" in result.phase2_meta
        ops = [d["op"] for d in result.diff]
        assert all(op in ("added", "merged", "rejected") for op in ops)
        # 真实落库验证
        memories = store.list_memories(limit=100)
        contents = [m["content"] for m in memories]
        assert "用户偏好用中文回复" in contents

    def test_phase2_llm_merge_into(self, store):
        existing = store.store(content="用户偏好英文回复", memory_type="semantic")
        p1 = json.dumps([{"content": "用户改口了，现在偏好中文回复", "importance": 0.8}])
        p2 = json.dumps([
            {"candidate_index": 0, "op": "MERGE_INTO",
             "target_memory_id": existing.memory_id,
             "merged_content": "用户偏好中文回复（早期记录为英文，已变更）",
             "reason": "同主题更新"},
        ])
        responses = iter([p1, p2])
        pipe = MemoryPipeline(ltm_store=store)
        # 两次调用依序返回Phase1/Phase2响应

        async def two_phase_llm(sys, usr):
            try:
                return next(responses)
            except StopIteration:
                return p2

        pipe._llm_call = two_phase_llm
        result = run(pipe.run(
            messages=[{"role": "user", "content": "我现在偏好中文回复"}],
            session_id="sess-merge",
        ))
        assert result.phase2_meta["phase2"] == "llm"
        entry = next(d for d in result.diff if d["memory_id"] == existing.memory_id)
        assert entry["op"] == "merged"
        assert entry["version_before"] == 1
        assert entry["version_after"] == 2
        updated = store.list_memories(limit=100)
        target = next(m for m in updated if m["memory_id"] == existing.memory_id)
        assert target["content"] == "用户偏好中文回复（早期记录为英文，已变更）"

    def test_phase2_update_and_reject(self, store):
        old = store.store(content="部署端口是8080", memory_type="semantic")
        candidates = [
            {"content": "部署端口改成了3000", "importance": 0.7},
            {"content": "天空是蓝色的", "importance": 0.2},
        ]
        p2 = json.dumps([
            {"candidate_index": 0, "op": "UPDATE", "target_memory_id": old.memory_id,
             "merged_content": "部署端口是3000（原8080已废弃）", "reason": "配置变更"},
            {"candidate_index": 1, "op": "REJECT", "reason": "常识无长期价值"},
        ])
        pipe = MemoryPipeline(ltm_store=store, llm_call=make_llm(p2))
        result = run(pipe.run(candidates=candidates, session_id="s", use_llm_phase2=True))
        diff_by_cand = {d["candidate"]: d for d in result.diff}
        upd = diff_by_cand["部署端口改成了3000"]
        assert upd["op"] == "updated"
        assert upd["version_after"] == 2
        rej = diff_by_cand["天空是蓝色的"]
        assert rej["op"] == "rejected"
        # UPDATE生效 + REJECT未落库
        mems = store.list_memories(limit=100)
        target = next(m for m in mems if m["memory_id"] == old.memory_id)
        assert target["content"] == "部署端口是3000（原8080已废弃）"
        assert not any(m["content"] == "天空是蓝色的" for m in mems)

    def test_import_mode_candidates_direct(self, store, pipeline):
        result = run(pipeline.run(
            candidates=[{"content": "直供候选事实X", "importance": 0.7}],
            session_id="sess-import",
            use_llm_phase2=False,
        ))
        assert result.mode == "import"
        assert result.phase1_count == 1
        assert result.phase2_meta["phase2"] == "deterministic"
        assert result.phase2_meta["phase2_fallback_reason"] == "use_llm_phase2=False"
        assert result.diff[0]["op"] == "added"
        contents = [m["content"] for m in store.list_memories(limit=100)]
        assert "直供候选事实X" in contents

    def test_no_input_error_visible(self, store, pipeline):
        result = run(pipeline.run())
        assert result.error == "no messages and no candidates"
        runs = pipeline.get_runs()
        assert runs[0]["error"] == "no messages and no candidates"

    def test_phase1_llm_failure_visible(self, store):
        pipe = MemoryPipeline(ltm_store=store, llm_call=FailingLLM())
        result = run(pipe.run(messages=[{"role": "user", "content": "hi"}], session_id="s"))
        assert "Phase1 LLM call failed" in result.error
        assert store.list_memories(limit=100) == [] or all(
            "管线" not in m["content"] for m in store.list_memories(limit=100)
        )

    def test_phase2_llm_failure_falls_back_visible(self, store):
        p1 = json.dumps([{"content": "降级路径测试事实", "importance": 0.6}])
        call_count = {"n": 0}

        async def flaky(sys, usr):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return p1
            raise RuntimeError("phase2 provider down")

        pipe = MemoryPipeline(ltm_store=store, llm_call=flaky)
        result = run(pipe.run(messages=[{"role": "user", "content": "x"}], session_id="s"))
        assert result.phase2_meta["phase2"] == "deterministic"
        assert "llm_error" in result.phase2_meta["phase2_fallback_reason"]
        # 降级后候选仍经merge策略落库（数据不丢）
        assert result.diff[0]["op"] == "added"
        contents = [m["content"] for m in store.list_memories(limit=100)]
        assert "降级路径测试事实" in contents

    def test_dry_run_no_writes(self, store, pipeline):
        before = len(store.list_memories(limit=1000))
        result = run(pipeline.run(
            candidates=[{"content": "dry-run不应落库", "importance": 0.7}],
            apply=False, use_llm_phase2=False,
        ))
        assert result.diff[0]["op"] == "planned_add_new"
        assert len(store.list_memories(limit=1000)) == before

    def test_deterministic_duplicate_merges(self, store, pipeline):
        store.store(content="完全重复的管线事实", memory_type="semantic")
        result = run(pipeline.run(
            candidates=[{"content": "完全重复的管线事实", "importance": 0.9}],
            use_llm_phase2=False,
        ))
        entry = result.diff[0]
        assert entry["op"] == "merged"  # fact_dedup并入而非追加
        assert entry["version_after"] >= 2
        matching = [m for m in store.list_memories(limit=100)
                    if m["content"] == "完全重复的管线事实"]
        assert len(matching) == 1  # 无重复条目

    def test_merge_target_not_found_rejected(self, store, pipeline):
        decisions = [ConsolidationDecision(
            op="MERGE_INTO",
            candidate=Phase1Candidate(content="孤儿候选"),
            target_memory_id="ltm_missing_target",
            merged_content="x",
            reason="目标缺失测试",
        )]
        diff = pipeline.apply(decisions, run_id="t")
        assert diff[0]["op"] == "rejected"
        assert "target_not_found" in diff[0]["reason"]

    def test_run_persisted_and_stats(self, store, pipeline):
        run(pipeline.run(
            candidates=[{"content": "持久化测试事实A", "importance": 0.6}],
            use_llm_phase2=False,
        ))
        runs = pipeline.get_runs()
        assert len(runs) >= 1
        latest = runs[0]
        assert latest["mode"] == "import"
        assert latest["phase1_count"] == 1
        assert isinstance(latest["diff"], list)
        assert latest["diff"][0]["op"] == "added"
        stats = pipeline.get_stats()
        assert stats["total_runs"] >= 1
        assert stats["diff_added"] >= 1
        assert stats["import_runs"] >= 1


# ── prune旧资源修剪 ──────────────────────────────────────────────

class TestPrune:
    def test_prune_old_transient_working(self, store, pipeline):
        old_time = time.time() - 100 * 3600  # 100小时前
        mem = store.store(
            content="任务：临时调试XYZ（过期工作记忆）",
            memory_type="working",
            safety_tags={"scope": "user", "durability": "transient", "authority": "descriptive"},
            write_mode="explicit",
        )
        # 手动把created_at改老（SQLite直接改）
        import sqlite3
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE memories SET created_at = ? WHERE memory_id = ?",
                (old_time, mem.memory_id),
            )
            conn.commit()
        # 一个新鲜的transient不应被剪
        store.store(
            content="任务：进行中的工作记忆",
            memory_type="working",
            safety_tags={"scope": "user", "durability": "transient", "authority": "descriptive"},
            write_mode="explicit",
        )
        # 一个过期但durable的不应被剪
        durable = store.store(
            content="过期但持久的用户事实",
            memory_type="semantic",
        )
        stats = pipeline.prune(max_age_hours=72.0, memory_type="working")
        assert stats["pruned"] == 1
        assert stats["blocked"] == 0
        active = store.list_memories(memory_type="working", limit=100)
        assert not any(m["memory_id"] == mem.memory_id for m in active)
        assert any(m["content"] == "任务：进行中的工作记忆" for m in active)
        # durable semantic未受影响（prune只看working）
        assert any(m["memory_id"] == durable.memory_id
                   for m in store.list_memories(limit=100))

    def test_prune_protected_scope_blocked(self, store, pipeline):
        import sqlite3
        mem = store.store(
            content="项目：门禁系统架构决策（受保护域）",
            memory_type="working",
            safety_tags={"scope": "task", "durability": "transient", "authority": "descriptive"},
            write_mode="explicit",
        )
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE memories SET created_at = ? WHERE memory_id = ?",
                (time.time() - 200 * 3600, mem.memory_id),
            )
            conn.commit()
        stats = pipeline.prune(max_age_hours=24.0, memory_type="working")
        assert stats["candidates"] == 1
        assert stats["blocked"] == 1  # DeerMem删除门fail-closed拦截task域
        assert stats["pruned"] == 0


# ── job handler接线 ──────────────────────────────────────────────

class TestJobHandlerWiring:
    def test_handler_registered(self, pipeline):
        assert "hippo.memory_pipeline" in HANDLER_SPECS
        names = register_default_handlers(jq=_FakeQueue())
        assert "hippo.memory_pipeline" in names

    def test_handler_executes_pipeline(self, store, pipeline, monkeypatch):
        import src.api.hippo as api_hippo
        monkeypatch.setattr(api_hippo, "_memory_pipeline", pipeline)
        handler = HANDLER_SPECS["hippo.memory_pipeline"]
        result = run(handler(
            candidates=[{"content": "作业队列管线测试事实", "importance": 0.7}],
            session_id="job-sess",
            apply=True,
            use_llm_phase2=False,
        ))
        assert result["mode"] == "import"
        assert result["diff"][0]["op"] == "added"
        contents = [m["content"] for m in store.list_memories(limit=100)]
        assert "作业队列管线测试事实" in contents


class _FakeQueue:
    def __init__(self):
        self.handlers = {}

    def register_handler(self, name, fn):
        self.handlers[name] = fn


# ── P0修复回归：provider响应解包（router.extract_chat_text权威解包点）────
# live实证bug：chat()返回OpenAI原始响应体choices[0].message.content，
# 旧代码result.get("content")落空→str(整个响应体)→解析0条且无error。

class TestProviderResponseUnwrap:
    def test_extract_chat_text_openai_body(self):
        from src.gland.router import extract_chat_text
        body = {"choices": [{"finish_reason": "stop", "index": 0,
                              "message": {"content": "正文内容", "role": "assistant"}}]}
        assert extract_chat_text(body) == "正文内容"

    def test_extract_chat_text_flat_str_and_unknown(self):
        from src.gland.router import extract_chat_text
        assert extract_chat_text({"content": "直接内容"}) == "直接内容"
        assert extract_chat_text({"text": "text字段"}) == "text字段"
        assert extract_chat_text("已是字符串") == "已是字符串"
        # 无法识别的dict形状→repr保留（失败可见，不静默空串）
        assert extract_chat_text({"choices": []}).startswith("{")

    def test_pipeline_openai_shaped_llm_response(self, store):
        openai_body = {"choices": [{"message": {"content": PHASE1_RESP, "role": "assistant"}}]}
        pipe = MemoryPipeline(ltm_store=store, llm_call=make_llm(openai_body))
        result = run(pipe.run(
            messages=[{"role": "user", "content": "以后请用中文回复，项目用Python 3.12"}],
            session_id="s-unwrap",
        ))
        assert result.error == ""
        assert result.phase1_count == 2  # 解包后Phase1候选正确解析（修复前=0）
        contents = [m["content"] for m in store.list_memories(limit=100)]
        assert "用户偏好用中文回复" in contents
        # extract端点可观测：raw_response即解包后的JSON（非响应体repr）
        p1 = run(pipe.extract(messages=[{"role": "user", "content": "x"}]))
        assert "候选" in p1.raw_response or "用户偏好" in p1.raw_response
        assert "choices" not in p1.raw_response  # 不再是整个响应体的repr

    def test_dream_openai_shaped_response(self, store):
        from src.hippo.dream_distiller import DreamDistiller
        dream_actions = json.dumps([
            {"action": "ADD", "content": "解包修复后入库的记忆", "memory_type": "semantic",
             "importance": 0.6, "reason": "unwrap回归"},
        ])
        openai_body = {"choices": [{"message": {"content": dream_actions, "role": "assistant"}}]}
        dd = DreamDistiller(ltm_store=store, llm_call=make_llm(openai_body))
        result = run(dd.dream(messages=[{"role": "user", "content": "记住：解包修复后入库的记忆"}]))
        assert result.error == ""
        assert result.applied == 1  # 修复前：gland形状→0 actions
        contents = [m["content"] for m in store.list_memories(limit=100)]
        assert "解包修复后入库的记忆" in contents
        d = result.to_dict()
        assert "raw_response" in d  # 失败可见字段
