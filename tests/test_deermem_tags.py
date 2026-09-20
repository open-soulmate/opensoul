"""Tests for DeerMem memory safety tags + fact_dedup merge gate.

调研来源：18-deer-flow-source.md #10/#11 + PROGRESS.md DeerMem条目 + SUMMARY.md P0-6
- #11 记忆抽取安全标签："抽取提议必须带scope/durability/authority，自动写只接受
  user-scoped+durable+descriptive；矛盾删除带reason+replacement，task/project域
  删除fail-closed"
- #10 写侧近重复fact门（fact_dedup）："新fact与同类别现有fact释义重复→并入
  （保留原id/confidence取max）而非追加"

Covers:
- infer_tags确定性推断（默认组合/working→transient/任务/项目/规范/更正标记）
- validate_write_tags：auto只接受user+durable+descriptive / explicit放行合法组合 /
  显式标签非法fail-closed / 缺失标签推断补齐
- validate_delete_tags：矛盾事实需reason+replacement / task/project域auto删除
  fail-closed / explicit人工路径放行
- store()集成：TAG_REJECT审计可见 / metadata落deermem_tags / fact_dedup同类别并入
  （保留原id+importance取max+MERGE审计）/ 跨类别近重复reject标注 / 默认reject回归
- delete_memory()集成：auto模式task域拦截+DELETE_BLOCKED审计 / explicit人工可删
- dream接线：ADD三标签/非auto组合skipped可见/近重复ADD并入/DELETE auto过门
- get_gatekeeper_stats deermem节可观测
"""

import asyncio
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.hippo.extractors.deermem_tags import (
    SafetyTags,
    infer_tags,
    tags_vocab,
    validate_delete_tags,
    validate_write_tags,
)
from src.hippo.long_term_memory import LongTermMemoryStore


def _make_store(enabled: bool = True) -> LongTermMemoryStore:
    tmp = tempfile.mktemp(suffix=".db")
    return LongTermMemoryStore(db_path=tmp, gatekeeper_enabled=enabled)


AUTO_OK = {"scope": "user", "durability": "durable", "authority": "descriptive"}
PROJECT_TAGS = {"scope": "project", "durability": "durable", "authority": "descriptive"}
TASK_TAGS = {"scope": "task", "durability": "durable", "authority": "descriptive"}
CONTRA_TAGS = {
    "scope": "user",
    "durability": "durable",
    "authority": "contradiction",
}


# ── infer_tags 确定性推断 ──────────────────────────────────────


class TestInferTags:
    def test_default_combo(self):
        t = infer_tags("用户偏好Python而不是Rust")
        assert (t.scope, t.durability, t.authority) == ("user", "durable", "descriptive")
        assert t.provenance == "inferred"

    def test_working_type_transient(self):
        t = infer_tags("正在处理的上下文", memory_type="working")
        assert t.durability == "transient"

    def test_task_marker(self):
        t = infer_tags("任务：完成招标文件编写")
        assert t.scope == "task"

    def test_project_marker(self):
        t = infer_tags("项目：OpenSoul仓库升级计划")
        assert t.scope == "project"

    def test_prescriptive_marker(self):
        t = infer_tags("规则：禁止在生产环境执行rm -rf")
        assert t.authority == "prescriptive"

    def test_contradiction_marker(self):
        t = infer_tags("更正：用户服务器已迁移到北京")
        assert t.authority == "contradiction"

    def test_no_marker_stays_user(self):
        t = infer_tags("用户喜欢在早上学习机器学习")
        assert t.scope == "user"
        assert t.authority == "descriptive"

    def test_latin_marker_case_insensitive(self):
        t = infer_tags("TODO: finish the deployment report")
        assert t.scope == "task"


# ── validate_write_tags 写侧安全标签门 ─────────────────────────


class TestValidateWriteTags:
    def test_explicit_auto_writable_accepted(self):
        d = validate_write_tags(AUTO_OK, write_mode="auto")
        assert d.accepted is True
        assert d.rule == "ok"
        assert d.tags.provenance == "explicit"

    def test_auto_rejects_project_scope(self):
        d = validate_write_tags(PROJECT_TAGS, write_mode="auto")
        assert d.accepted is False
        assert d.rule == "requires_explicit_confirmation"
        assert "scope" in d.reason
        assert d.tags is not None and d.tags.scope == "project"

    def test_auto_rejects_transient_durability(self):
        tags = {"scope": "user", "durability": "transient", "authority": "descriptive"}
        d = validate_write_tags(tags, write_mode="auto")
        assert d.accepted is False
        assert d.rule == "requires_explicit_confirmation"

    def test_malformed_scope_fail_closed(self):
        d = validate_write_tags(
            {"scope": "galaxy", "durability": "durable", "authority": "descriptive"},
            write_mode="auto",
        )
        assert d.accepted is False
        assert d.rule == "invalid_scope"
        assert d.tags is None

    def test_missing_field_fail_closed(self):
        d = validate_write_tags({"scope": "user", "authority": "descriptive"}, write_mode="auto")
        assert d.accepted is False
        assert d.rule == "invalid_durability"

    def test_explicit_mode_allows_project(self):
        d = validate_write_tags(PROJECT_TAGS, write_mode="explicit")
        assert d.accepted is True
        assert d.rule == "ok"

    def test_explicit_mode_still_rejects_malformed(self):
        d = validate_write_tags(
            {"scope": "user", "durability": "forever", "authority": "descriptive"},
            write_mode="explicit",
        )
        assert d.accepted is False
        assert d.rule == "invalid_durability"

    def test_none_tags_inferred_defaults_accepted(self):
        d = validate_write_tags(None, content="用户偏好深色主题", write_mode="auto")
        assert d.accepted is True
        assert d.rule == "ok_inferred"
        assert d.tags.provenance == "inferred"

    def test_none_tags_working_type_rejected(self):
        d = validate_write_tags(None, content="上下文片段", memory_type="working")
        assert d.accepted is False
        assert d.rule == "requires_explicit_confirmation"

    def test_safety_tags_instance_coerce(self):
        st = SafetyTags(scope="user", durability="durable", authority="descriptive")
        d = validate_write_tags(st, write_mode="auto")
        assert d.accepted is True


# ── validate_delete_tags 删除安全门 ────────────────────────────


class TestValidateDeleteTags:
    def test_contradiction_without_replacement_blocked(self):
        d = validate_delete_tags(CONTRA_TAGS, reason="outdated", replacement="", mode="auto")
        assert d.accepted is False
        assert d.rule == "replacement_required"

    def test_contradiction_explicit_without_replacement_blocked(self):
        """DeerMem #11：矛盾删除reason+replacement两种模式都强制。"""
        d = validate_delete_tags(CONTRA_TAGS, reason="outdated", mode="explicit")
        assert d.accepted is False
        assert d.rule == "replacement_required"

    def test_contradiction_with_replacement_accepted(self):
        d = validate_delete_tags(
            CONTRA_TAGS,
            reason="server migrated",
            replacement="服务器已迁移到北京机房",
            mode="auto",
        )
        assert d.accepted is True

    def test_task_scope_auto_blocked(self):
        d = validate_delete_tags(TASK_TAGS, reason="done", mode="auto")
        assert d.accepted is False
        assert d.rule == "protected_scope"

    def test_project_scope_auto_blocked(self):
        d = validate_delete_tags(PROJECT_TAGS, reason="obsolete", mode="auto")
        assert d.accepted is False
        assert d.rule == "protected_scope"

    def test_task_scope_explicit_allowed(self):
        d = validate_delete_tags(TASK_TAGS, reason="done", mode="explicit")
        assert d.accepted is True

    def test_user_scope_auto_allowed(self):
        d = validate_delete_tags(AUTO_OK, reason="stale", mode="auto")
        assert d.accepted is True

    def test_none_tags_inferred_from_content(self):
        d = validate_delete_tags(None, content="Outdated info", mode="auto")
        assert d.accepted is True
        assert d.tags.provenance == "inferred"

    def test_malformed_stored_tags_fail_closed(self):
        d = validate_delete_tags(
            {"scope": "bogus", "durability": "durable", "authority": "descriptive"},
            content="x",
            mode="explicit",
        )
        assert d.accepted is False
        assert d.rule == "invalid_scope"


# ── store() 集成：标签门 + fact_dedup ─────────────────────────


class TestStoreTagGate:
    def test_auto_project_tags_rejected_with_audit(self):
        store = _make_store()
        mem = store.store(content="OpenSoul部署拓扑说明文档", safety_tags=PROJECT_TAGS)
        assert mem is None
        assert store.last_write_outcome == "rejected_tags"
        history = store.get_history(event="TAG_REJECT")
        assert len(history) == 1
        detail = json.loads(history[0]["new_value"])
        assert detail["rule"] == "requires_explicit_confirmation"
        assert history[0]["reason"] == "deermem_tags:requires_explicit_confirmation"
        assert store.list_memories() == []

    def test_valid_tags_stored_with_metadata(self):
        store = _make_store()
        mem = store.store(content="用户偏好使用vim进行编辑", safety_tags=AUTO_OK)
        assert mem is not None
        assert store.last_write_outcome == "added"
        tags = mem.metadata["deermem_tags"]
        assert tags["scope"] == "user"
        assert tags["provenance"] == "explicit"
        # ADD审计携带deermem_tags
        history = store.get_history(memory_id=mem.memory_id, event="ADD")
        assert json.loads(history[0]["new_value"])["deermem_tags"]["scope"] == "user"

    def test_no_tags_inferred_recorded(self):
        store = _make_store()
        mem = store.store(content="用户喜欢在早上学习机器学习")
        assert mem is not None
        assert mem.metadata["deermem_tags"]["provenance"] == "inferred"
        assert mem.metadata["deermem_tags"]["scope"] == "user"

    def test_explicit_mode_allows_project(self):
        store = _make_store()
        mem = store.store(
            content="OpenSoul部署拓扑说明文档",
            safety_tags=PROJECT_TAGS,
            write_mode="explicit",
        )
        assert mem is not None
        assert mem.metadata["deermem_tags"]["scope"] == "project"

    def test_malformed_explicit_tags_rejected(self):
        store = _make_store()
        mem = store.store(
            content="Some memory content here",
            safety_tags={"scope": "???"},
            write_mode="explicit",
        )
        assert mem is None
        history = store.get_history(event="TAG_REJECT")
        assert json.loads(history[0]["new_value"])["rule"] == "invalid_scope"

    def test_force_bypasses_tag_gate(self):
        store = _make_store()
        mem = store.store(
            content="OpenSoul部署拓扑说明文档",
            safety_tags=PROJECT_TAGS,
            force=True,
        )
        assert mem is not None  # force=人工覆盖，不被标签门拦
        assert store.last_write_outcome == "added"


class TestFactDedupMerge:
    def test_exact_duplicate_merges_same_category(self):
        store = _make_store()
        first = store.store(
            content="Deployment pipeline uses blue-green strategy",
            memory_type="semantic",
            importance=0.4,
        )
        assert first is not None
        second = store.store(
            content="Deployment pipeline uses blue-green strategy",
            memory_type="semantic",
            importance=0.8,
            dup_policy="merge",
        )
        assert second is not None
        assert second.memory_id == first.memory_id  # 保留原id
        assert second.importance == 0.8  # confidence取max
        assert len(store.list_memories()) == 1  # 并入而非追加
        assert store.last_write_outcome == "merged"
        history = store.get_history(event="MERGE")
        assert len(history) == 1
        assert history[0]["reason"] == "fact_dedup:duplicate_exact"
        assert history[0]["memory_id"] == first.memory_id
        detail = json.loads(history[0]["new_value"])
        assert detail["importance"] == 0.8
        # 被并入内容留痕
        listed = store.list_memories()[0]
        assert len(listed["metadata"]["deermem_merges"]) == 1

    def test_near_duplicate_merges_cjk(self):
        store = _make_store()
        first = store.store(
            content="数据库迁移方案设计与实施步骤",
            memory_type="semantic",
            importance=0.5,
        )
        second = store.store(
            content="数据库迁移方案设计与实施步骤说明",
            memory_type="semantic",
            importance=0.6,
            dup_policy="merge",
        )
        assert second is not None
        assert second.memory_id == first.memory_id
        assert len(store.list_memories()) == 1

    def test_merge_keeps_max_importance(self):
        """新fact importance更低时，既有importance不被拉低（取max）。"""
        store = _make_store()
        store.store(
            content="Kubernetes autoscaling configuration notes",
            memory_type="semantic",
            importance=0.9,
        )
        second = store.store(
            content="Kubernetes autoscaling configuration notes",
            memory_type="semantic",
            importance=0.2,
            dup_policy="merge",
        )
        assert second is not None
        assert second.importance == 0.9

    def test_cross_category_near_dup_rejected_not_merged(self):
        """DeerMem fact_dedup只并入同类别fact——跨类别近重复维持reject且可见。"""
        store = _make_store()
        first = store.store(
            content="Deployment pipeline uses blue-green strategy",
            memory_type="semantic",
        )
        assert first is not None
        second = store.store(
            content="Deployment pipeline uses blue-green strategy",
            memory_type="episodic",
            dup_policy="merge",
        )
        assert second is None
        assert store.last_write_outcome == "rejected_gate"
        assert len(store.list_memories()) == 1
        history = store.get_history(event="GATE_REJECT")
        detail = json.loads(history[0]["new_value"])
        assert "cross_category_not_mergeable" in detail["reason"]

    def test_default_policy_rejects_duplicate_regression(self):
        store = _make_store()
        store.store(content="Some unique fact worth remembering")
        second = store.store(content="Some unique fact worth remembering")
        assert second is None
        assert store.last_write_outcome == "rejected_gate"
        history = store.get_history(event="GATE_REJECT")
        assert json.loads(history[0]["new_value"])["rule"] == "duplicate_exact"

    def test_merge_rejected_when_tags_not_auto_writable(self):
        """非auto-writable标签的提议即使近重复也不并入（标签门先拦）。"""
        store = _make_store()
        store.store(
            content="Deployment pipeline uses blue-green strategy",
            memory_type="semantic",
        )
        second = store.store(
            content="Deployment pipeline uses blue-green strategy",
            memory_type="semantic",
            dup_policy="merge",
            safety_tags=PROJECT_TAGS,
        )
        assert second is None
        assert store.last_write_outcome == "rejected_tags"
        assert len(store.list_memories()) == 1


# ── delete_memory() 删除安全门集成 ─────────────────────────────


class TestDeleteGate:
    def test_task_fact_auto_delete_blocked(self):
        store = _make_store()
        mem = store.store(
            content="任务：完成三季度招标文件归档",
            safety_tags=TASK_TAGS,
            write_mode="explicit",
        )
        assert mem is not None
        ok = store.delete_memory(mem.memory_id, reason="done", delete_mode="auto")
        assert ok is False
        assert store.last_delete_decision.rule == "protected_scope"
        history = store.get_history(event="DELETE_BLOCKED")
        assert len(history) == 1
        assert history[0]["reason"] == "deermem_tags:protected_scope"
        # 记忆仍然活跃
        assert len(store.list_memories()) == 1

    def test_task_fact_explicit_delete_allowed(self):
        store = _make_store()
        mem = store.store(
            content="任务：完成三季度招标文件归档",
            safety_tags=TASK_TAGS,
            write_mode="explicit",
        )
        ok = store.delete_memory(mem.memory_id, reason="user done", delete_mode="explicit")
        assert ok is True
        assert store.list_memories() == []

    def test_contradiction_delete_requires_replacement(self):
        store = _make_store()
        mem = store.store(
            content="更正：用户服务器已迁移到北京",
            safety_tags=CONTRA_TAGS,
            write_mode="explicit",
        )
        ok = store.delete_memory(mem.memory_id, reason="superseded", delete_mode="explicit")
        assert ok is False
        assert store.last_delete_decision.rule == "replacement_required"
        ok2 = store.delete_memory(
            mem.memory_id,
            reason="superseded",
            delete_mode="explicit",
            replacement="服务器已迁移到北京机房",
        )
        assert ok2 is True

    def test_legacy_memory_without_tags_delete_ok(self):
        """无标签存量记忆（老库）：内容无保护域标记→推断user域，auto删除放行。"""
        store = _make_store()
        mem = store.store(content="Outdated info")
        # 模拟存量记忆：清掉deermem_tags（改动前入库的记忆无此字段）
        store.update_memory(mem.memory_id, metadata={})
        ok = store.delete_memory(mem.memory_id, reason="stale", delete_mode="auto")
        assert ok is True

    def test_delete_decision_reset_on_missing_id(self):
        store = _make_store()
        ok = store.delete_memory("ltm_nonexistent", delete_mode="auto")
        assert ok is False
        assert store.last_delete_decision is None  # 不残留上次判定


# ── dream_distiller 接线 ──────────────────────────────────────


def _make_dream(store, response: str):
    from src.hippo.dream_distiller import DreamDistiller

    async def mock_llm(sp, up):
        return response

    return DreamDistiller(ltm_store=store, llm_call=mock_llm)


class TestDreamWiring:
    def test_add_with_three_tags_stored_explicit_provenance(self):
        store = _make_store()
        response = json.dumps(
            [
                {
                    "action": "ADD",
                    "content": "用户偏好使用增量diff方式修改代码",
                    "memory_type": "semantic",
                    "importance": 0.7,
                    "scope": "user",
                    "durability": "durable",
                    "authority": "descriptive",
                    "reason": "用户明确表达的开发偏好",
                }
            ]
        )
        distiller = _make_dream(store, response)
        result = asyncio.run(distiller.dream(messages=[{"role": "user", "content": "hi"}]))
        assert result.applied == 1
        stored = store.list_memories()
        assert stored[0]["metadata"]["deermem_tags"]["provenance"] == "explicit"

    def test_add_non_auto_tags_skipped_visible(self):
        store = _make_store()
        response = json.dumps(
            [
                {
                    "action": "ADD",
                    "content": "OpenSoul服务部署在8090端口",
                    "memory_type": "semantic",
                    "scope": "project",
                    "durability": "durable",
                    "authority": "descriptive",
                    "reason": "项目事实",
                }
            ]
        )
        distiller = _make_dream(store, response)
        result = asyncio.run(distiller.dream(messages=[{"role": "user", "content": "hi"}]))
        assert result.applied == 0
        assert result.skipped == 1  # 安全门拒绝→skipped可见
        assert store.list_memories() == []
        history = store.get_history(event="TAG_REJECT")
        assert len(history) == 1

    def test_add_partial_tags_fail_closed(self):
        """只给部分标签→fail-closed拒绝（抽取提议必须带三标签）。"""
        store = _make_store()
        response = json.dumps(
            [
                {
                    "action": "ADD",
                    "content": "用户偏好Python",
                    "memory_type": "semantic",
                    "scope": "user",
                    "reason": "只给了scope",
                }
            ]
        )
        distiller = _make_dream(store, response)
        result = asyncio.run(distiller.dream(messages=[{"role": "user", "content": "hi"}]))
        assert result.skipped == 1
        history = store.get_history(event="TAG_REJECT")
        assert json.loads(history[0]["new_value"])["rule"] == "invalid_durability"

    def test_add_near_dup_merges_not_appends(self):
        store = _make_store()
        store.store(
            content="数据库迁移方案设计与实施步骤",
            memory_type="semantic",
            importance=0.5,
        )
        response = json.dumps(
            [
                {
                    "action": "ADD",
                    "content": "数据库迁移方案设计与实施步骤说明",
                    "memory_type": "semantic",
                    "importance": 0.7,
                    "reason": "蒸馏发现",
                },
                {
                    "action": "ADD",
                    "content": "数据库迁移方案设计与实施步骤说明",
                    "memory_type": "semantic",
                    "importance": 0.6,
                    "reason": "重复提议",
                },
            ]
        )
        distiller = _make_dream(store, response)
        result = asyncio.run(distiller.dream(messages=[{"role": "user", "content": "hi"}]))
        assert result.applied == 2  # 首条也命中近重复→并入；次条继续并入
        assert len(store.list_memories()) == 1
        history = store.get_history(event="MERGE")
        assert len(history) == 2
        assert all(h["reason"].startswith("fact_dedup:") for h in history)

    def test_delete_task_fact_blocked_by_auto_gate(self):
        store = _make_store()
        mem = store.store(
            content="任务：完成三季度招标文件归档",
            safety_tags=TASK_TAGS,
            write_mode="explicit",
        )
        response = json.dumps(
            [{"action": "DELETE", "memory_id": mem.memory_id, "reason": "看起来过时"}]
        )
        distiller = _make_dream(store, response)
        result = asyncio.run(distiller.dream(messages=[{"role": "user", "content": "hi"}]))
        assert result.skipped == 1  # 自动路径删除task域fact被fail-closed拦截
        assert len(store.list_memories()) == 1  # fact仍在

    def test_delete_normal_fact_still_works(self):
        store = _make_store()
        mem = store.store(content="Outdated info")
        response = json.dumps(
            [{"action": "DELETE", "memory_id": mem.memory_id, "reason": "outdated"}]
        )
        distiller = _make_dream(store, response)
        result = asyncio.run(distiller.dream(messages=[{"role": "user", "content": "hi"}]))
        assert result.applied == 1
        assert store.list_memories() == []

    def test_parse_dream_action_tag_fields(self):
        from src.hippo.dream_distiller import _parse_dream_actions

        response = json.dumps(
            [
                {
                    "action": "ADD",
                    "content": "记忆内容",
                    "scope": "User",
                    "durability": "DURABLE",
                    "authority": "descriptive",
                }
            ]
        )
        actions = _parse_dream_actions(response)
        assert actions[0].scope == "user"  # lowercased
        assert actions[0].durability == "durable"
        assert actions[0].authority == "descriptive"

    def test_parse_missing_tag_fields_empty(self):
        from src.hippo.dream_distiller import _parse_dream_actions

        response = json.dumps([{"action": "ADD", "content": "记忆内容"}])
        actions = _parse_dream_actions(response)
        assert actions[0].scope == ""
        assert actions[0].durability == ""
        assert actions[0].authority == ""


# ── 可观测性 ──────────────────────────────────────────────────


class TestObservability:
    def test_gatekeeper_stats_deermem_section(self):
        store = _make_store()
        store.store(content="ok content admitted here")
        store.store(content="OpenSoul部署拓扑说明文档", safety_tags=PROJECT_TAGS)  # TAG_REJECT
        mem = store.store(
            content="任务：归档旧合同",
            safety_tags=TASK_TAGS,
            write_mode="explicit",
        )
        store.delete_memory(mem.memory_id, reason="x", delete_mode="auto")  # DELETE_BLOCKED
        store.store(content="A completely distinct fact about kubernetes")
        store.store(
            content="A completely distinct fact about kubernetes",
            dup_policy="merge",
        )  # MERGE fact_dedup
        stats = store.get_gatekeeper_stats()
        dm = stats["deermem"]
        assert dm["tag_rejected"] == 1
        assert dm["delete_blocked"] == 1
        assert dm["fact_dedup_merged"] == 1
        assert dm["last_write_outcome"] == "merged"
        events = {b["event"] for b in dm["recent_blocks"]}
        assert events == {"TAG_REJECT", "DELETE_BLOCKED"}

    def test_tags_vocab(self):
        v = tags_vocab()
        assert "user" in v["scopes"]
        assert v["auto_writable"] == {
            "scope": "user",
            "durability": "durable",
            "authority": "descriptive",
        }
        assert set(v["protected_delete_scopes"]) == {"task", "project"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
