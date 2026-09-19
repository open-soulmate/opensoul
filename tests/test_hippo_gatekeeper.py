"""Tests for hippo memory admission gatekeeper (LobeChat pattern).

调研来源：26-lobe-chat-source.md §6 — gatekeeper（守门员：判断这条该不该进长期记忆），
"不加过滤的记忆=垃圾堆积"。

Covers:
- MemoryGatekeeper规则单测（确定性判定，无LLM）
- store()接入：reject不入库+返回None+GATE_REJECT审计（mem0 §1.1失败可见）
- force=True旁路 / gatekeeper_enabled=False旁路
- 重复判定：exact + near（CJK bigram + token Jaccard）
- get_gatekeeper_stats()可观测性
- 调用方接线：dream ADD被拒时计入skipped不冒充成功
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.hippo.extractors.gatekeeper import GateDecision, MemoryGatekeeper
from src.hippo.long_term_memory import LongTermMemoryStore


def _make_store(enabled: bool = True) -> LongTermMemoryStore:
    tmp = tempfile.mktemp(suffix=".db")
    return LongTermMemoryStore(db_path=tmp, gatekeeper_enabled=enabled)


# ── Gatekeeper规则单测 ────────────────────────────────────────


class TestGatekeeperRules:
    def test_admits_normal_content(self):
        gk = MemoryGatekeeper()
        d = gk.evaluate("用户偏好Python而不是Rust")
        assert d.verdict == "admit"
        assert d.rule == "ok"

    def test_rejects_empty(self):
        gk = MemoryGatekeeper()
        d = gk.evaluate("   ")
        assert d.verdict == "reject"
        assert d.rule == "empty_content"

    def test_rejects_too_short(self):
        gk = MemoryGatekeeper()
        d = gk.evaluate("hi")
        assert d.verdict == "reject"
        assert d.rule == "too_short"

    def test_admits_min_boundary(self):
        """len==min_length(4)的内容准入（既有测试夹具如"Test"依赖此边界）。"""
        gk = MemoryGatekeeper()
        d = gk.evaluate("Test")
        assert d.verdict == "admit"

    def test_rejects_pure_digits(self):
        gk = MemoryGatekeeper()
        d = gk.evaluate("12345 678")
        assert d.verdict == "reject"
        assert d.rule == "no_meaningful_content"

    def test_rejects_secret_api_key(self):
        gk = MemoryGatekeeper()
        d = gk.evaluate("my api_key = abcd1234efgh5678 for production")
        assert d.verdict == "reject"
        assert d.rule == "secret_detected"
        # 凭证原文不得写进reason（防二次泄露）
        assert "abcd1234efgh5678" not in d.reason

    def test_rejects_secret_sk_key(self):
        gk = MemoryGatekeeper()
        d = gk.evaluate(f"openai key here: sk-{'a' * 32}")
        assert d.verdict == "reject"
        assert d.rule == "secret_detected"

    def test_rejects_control_char_noise(self):
        gk = MemoryGatekeeper()
        d = gk.evaluate("ok\x01\x02\x03\x04\x05\x06\x07 fine")
        assert d.verdict == "reject"
        assert d.rule == "control_char_noise"

    def test_rejects_exact_duplicate(self):
        gk = MemoryGatekeeper()
        d = gk.evaluate(
            "User prefers Python over Rust",
            recent_contents=[("ltm_a", "User prefers Python over Rust")],
        )
        assert d.verdict == "reject"
        assert d.rule == "duplicate_exact"
        assert d.duplicate_of == "ltm_a"

    def test_exact_duplicate_normalizes_whitespace_case(self):
        gk = MemoryGatekeeper()
        d = gk.evaluate(
            "  user prefers python over rust ",
            recent_contents=[("ltm_a", "User prefers Python over Rust")],
        )
        assert d.verdict == "reject"
        assert d.rule == "duplicate_exact"

    def test_rejects_near_duplicate_latin(self):
        gk = MemoryGatekeeper()
        base = "kubernetes deployment configuration for production cluster"
        cand = "kubernetes deployment configuration for production cluster setup"
        d = gk.evaluate(cand, recent_contents=[("ltm_b", base)])
        assert d.verdict == "reject"
        assert d.rule == "duplicate_near"
        assert d.similarity >= 0.85

    def test_plural_variant_below_threshold_admitted(self):
        """诚实边界：词形变化(单复数)会拉低Jaccard(5/7≈0.71)——
        确定性规则不做词干化，此为已知限制（报告遗留问题）。"""
        gk = MemoryGatekeeper()
        base = "kubernetes deployment configuration for production cluster"
        cand = "kubernetes deployment configuration for production clusters"
        d = gk.evaluate(cand, recent_contents=[("ltm_b", base)])
        assert d.verdict == "admit"

    def test_rejects_near_duplicate_cjk_bigram(self):
        gk = MemoryGatekeeper()
        base = "数据库迁移方案设计与实施步骤"
        cand = "数据库迁移方案设计与实施步骤说明"
        d = gk.evaluate(cand, recent_contents=[("ltm_c", base)])
        assert d.verdict == "reject"
        assert d.rule == "duplicate_near"

    def test_admits_distinct_content(self):
        gk = MemoryGatekeeper()
        d = gk.evaluate(
            "前端界面需要低饱和配色方案",
            recent_contents=[("ltm_d", "后端数据库索引优化策略")],
        )
        assert d.verdict == "admit"

    def test_skips_duplicate_check_without_candidates(self):
        """recent_contents=None时跳过重复判定（gatekeeper不查库）。"""
        gk = MemoryGatekeeper()
        d = gk.evaluate("Some content that might duplicate")
        assert d.verdict == "admit"

    def test_force_bypasses_all_rules(self):
        gk = MemoryGatekeeper()
        d = gk.evaluate("sk-" + "a" * 32, force=True)
        assert d.verdict == "admit"
        assert d.rule == "bypass"

    def test_disabled_bypasses(self):
        gk = MemoryGatekeeper(enabled=False)
        d = gk.evaluate("")
        assert d.verdict == "admit"
        assert d.rule == "bypass"

    def test_stats_track_rejections_by_rule(self):
        gk = MemoryGatekeeper()
        gk.evaluate("")
        gk.evaluate("hi")
        gk.evaluate("正常内容可以通过")
        stats = gk.stats()
        assert stats["admitted"] == 1
        assert stats["rejected"] == 2
        assert stats["rejected_by_rule"]["empty_content"] == 1
        assert stats["rejected_by_rule"]["too_short"] == 1

    def test_gate_decision_to_dict(self):
        d = GateDecision("reject", "too_short", "len<4")
        dd = d.to_dict()
        assert dd["verdict"] == "reject"
        assert dd["rule"] == "too_short"
        json.dumps(dd)  # 必须可序列化（API返回）


# ── store()接入 ───────────────────────────────────────────────


class TestStoreGatekeeperWiring:
    def test_store_admits_and_returns_memory(self):
        store = _make_store()
        mem = store.store(content="User works in Beijing office", memory_type="semantic")
        assert mem is not None
        assert mem.content == "User works in Beijing office"

    def test_store_reject_returns_none_and_not_stored(self):
        store = _make_store()
        mem = store.store(content="my password = supersecret9999")
        assert mem is None
        assert store.list_memories() == []

    def test_store_reject_writes_gate_reject_audit(self):
        """mem0 §1.1：拒绝必须可见——GATE_REJECT审计而非静默丢弃。"""
        store = _make_store()
        store.store(content="ab")  # too_short
        history = store.get_history(event="GATE_REJECT")
        assert len(history) == 1
        detail = json.loads(history[0]["new_value"])
        assert detail["rule"] == "too_short"
        assert history[0]["reason"] == "gatekeeper:too_short"

    def test_store_duplicate_rejected_with_audit(self):
        store = _make_store()
        first = store.store(content="Deployment pipeline uses blue-green strategy")
        assert first is not None
        second = store.store(content="Deployment pipeline uses blue-green strategy")
        assert second is None
        history = store.get_history(event="GATE_REJECT")
        assert len(history) == 1
        detail = json.loads(history[0]["new_value"])
        assert detail["rule"] == "duplicate_exact"
        assert detail["duplicate_of"] == first.memory_id

    def test_store_force_bypasses_gatekeeper(self):
        store = _make_store()
        mem = store.store(content="Duplicate allowed via force", force=True)
        assert mem is not None
        mem2 = store.store(content="Duplicate allowed via force", force=True)
        assert mem2 is not None
        assert len(store.list_memories()) == 2

    def test_store_disabled_gatekeeper_allows_garbage(self):
        store = _make_store(enabled=False)
        mem = store.store(content="")
        assert mem is not None  # gatekeeper关闭时行为回退到旧行为

    def test_gatekeeper_stats_endpoint_data(self):
        store = _make_store()
        store.store(content="ok content admitted here")
        store.store(content="hi")  # rejected: too_short
        stats = store.get_gatekeeper_stats()
        assert stats["enabled"] is True
        assert stats["audit"]["total_rejected_records"] == 1
        assert "gatekeeper:too_short" in stats["audit"]["by_reason"]
        assert len(stats["audit"]["recent_rejections"]) == 1
        assert stats["last_decision"]["verdict"] in ("admit", "reject")


# ── 调用方接线：dream ADD被拒不冒充成功 ───────────────────────


class TestDreamCallerWiring:
    def test_dream_add_gate_rejected_counts_skipped(self):
        import asyncio
        import json as _json

        from src.hippo.dream_distiller import DreamDistiller

        store = _make_store()
        response = _json.dumps([
            {"action": "ADD", "content": "ab", "reason": "too short to keep"}
        ])

        async def mock_llm(sp, up):
            return response

        distiller = DreamDistiller(ltm_store=store, llm_call=mock_llm)
        result = asyncio.run(distiller.dream(messages=[{"role": "user", "content": "hi"}]))
        assert result.applied == 0
        assert result.skipped == 1  # 被gatekeeper拒绝→skipped可见，不是applied
        assert store.list_memories() == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
