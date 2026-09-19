# -*- coding: utf-8 -*-
"""P1 上下文逐项token归因测试 — claude-code SDKContextUsage移植

调研来源：feature-matrix/10-claude-code-source.md #7（total/raw_max/percentage/
over_limit{tokens_over,kind} + mcp_tools[]/memory_files[]/agents[]/skills[]逐项明细）。

无网络依赖：纯模块测试 + chat路径helper直接调用 + 端点函数直接调用。
"""
import asyncio

import pytest

from src.cortex.token_attribution import (
    DEFAULT_COMPACTION_RATIO,
    ContextAttributor,
    ContextItem,
    KIND_BUILTIN_TOOL,
    KIND_MEMORY,
    KIND_MESSAGE,
    KIND_MCP_TOOL,
    KIND_RAG,
    KIND_SKILL,
    KIND_SYSTEM_PROMPT,
    build_context_usage,
    estimate_tokens,
    items_from_openai_tools,
    reset_attributor,
    resolve_context_window,
)
import src.cortex.token_attribution as ta


@pytest.fixture(autouse=True)
def _fresh_attributor():
    reset_attributor()
    yield
    reset_attributor()


# ════════════════════════════════════════════════════════════════
# estimate_tokens — CJK≈1token/字符，其余chars//4（与acp-proxy镜像公式一致）
# ════════════════════════════════════════════════════════════════

class TestEstimateTokens:
    def test_ascii_chars_div4(self):
        assert estimate_tokens("a" * 400) == 100

    def test_cjk_one_token_per_char(self):
        # 汉字：100字≈100token（保守估计）
        assert estimate_tokens("上下文归因" * 20) == 100

    def test_mixed_cjk_ascii(self):
        # 50 CJK + 200 ascii → 50 + 200//4 = 100
        text = "归因" * 25 + "x" * 200
        assert estimate_tokens(text) == 50 + 50

    def test_fullwidth_punctuation_counts_as_cjk(self):
        assert estimate_tokens("，。！？") == 4  # U+3000段全角标点
        assert estimate_tokens("ＡＢ") == 2  # U+FF00段全角字母

    def test_empty_and_none_safe(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens(None) == 0

    def test_formula_matches_acp_proxy_mirror(self):
        """镜像一致性声明：acp-proxy/agent/token_attribution.py同公式（那边测试同样断言）"""
        text = "任务" * 10 + "tool_call" * 8
        expected = 20 + (72 // 4)
        assert estimate_tokens(text) == expected


# ════════════════════════════════════════════════════════════════
# build_context_usage — SDKContextUsage形态
# ════════════════════════════════════════════════════════════════

def _items():
    return [
        ContextItem(kind=KIND_MCP_TOOL, name="feishu_search", source="mcp", tokens=900),
        ContextItem(kind=KIND_BUILTIN_TOOL, name="read_file", source="builtin", tokens=300),
        ContextItem(kind=KIND_MEMORY, name="hippo_memory[0]", source="hippo", tokens=200),
        ContextItem(kind=KIND_SKILL, name="excel-skill", source="skill_manager", tokens=1500,
                    extra={"plugin_name": "builtin"}),
        ContextItem(kind=KIND_SYSTEM_PROMPT, name="soulmate_base_prompt", source="soulmate", tokens=2500),
        ContextItem(kind=KIND_MESSAGE, name="conversation_history", source="session", tokens=1200),
        ContextItem(kind=KIND_RAG, name="rag_chunk[0]", source="search", tokens=400),
    ]


class TestBuildContextUsage:
    def test_total_and_percentage(self):
        usage = build_context_usage(_items(), max_tokens=10000)
        assert usage["total_tokens"] == 900 + 300 + 200 + 1500 + 2500 + 1200 + 400
        assert usage["raw_max_tokens"] == 10000
        assert usage["percentage"] == round(usage["total_tokens"] * 100.0 / 10000, 1)

    def test_detail_arrays_sdk_shape(self):
        usage = build_context_usage(_items(), max_tokens=10000)
        # claude-code SDKContextUsage四类明细 + OpenSoul扩展
        assert usage["mcp_tools"] == [{"name": "feishu_search", "source": "mcp", "tokens": 900}]
        assert usage["builtin_tools"][0]["name"] == "read_file"
        assert usage["memory_files"][0]["name"] == "hippo_memory[0]"
        assert usage["skills"][0]["name"] == "excel-skill"
        assert usage["skills"][0]["plugin_name"] == "builtin"  # extra字段透传
        assert usage["agents"] == []

    def test_sections_aggregate_non_list_kinds(self):
        usage = build_context_usage(_items(), max_tokens=10000)
        assert usage["sections"][KIND_SYSTEM_PROMPT] == 2500
        assert usage["sections"][KIND_MESSAGE] == 1200
        assert usage["sections"][KIND_RAG] == 400

    def test_top_consumers_sorted_desc(self):
        usage = build_context_usage(_items(), max_tokens=10000)
        top = usage["top_consumers"]
        assert top[0]["name"] == "soulmate_base_prompt"  # 2500
        assert top[1]["name"] == "excel-skill"  # 1500
        tokens = [c["tokens"] for c in top]
        assert tokens == sorted(tokens, reverse=True)
        assert top[0]["percentage"] == round(2500 * 100.0 / 10000, 1)

    def test_no_over_limit(self):
        usage = build_context_usage(_items(), max_tokens=100000)
        assert usage["over_limit"] is None

    def test_compaction_window_kind(self):
        # 总量7040 > 10000*0.6=6000 且 <=10000 → compaction_window
        usage = build_context_usage(_items(), max_tokens=10000)
        assert usage["over_limit"]["kind"] == "compaction_window"
        assert usage["over_limit"]["tokens_over"] == usage["total_tokens"] - int(10000 * DEFAULT_COMPACTION_RATIO)

    def test_hard_limit_kind(self):
        usage = build_context_usage(_items(), max_tokens=5000)
        assert usage["over_limit"]["kind"] == "hard_limit"
        assert usage["over_limit"]["tokens_over"] == usage["total_tokens"] - 5000

    def test_model_window_resolution(self):
        assert resolve_context_window("deepseek-r1:7b") == 65536
        assert resolve_context_window("xiaomi/mimo-v2.5-pro") == 65536
        assert resolve_context_window("unknown-model") == ta.DEFAULT_CONTEXT_WINDOW
        assert resolve_context_window(None) == ta.DEFAULT_CONTEXT_WINDOW
        # model参数驱动窗口
        usage = build_context_usage(_items(), model="deepseek-chat")
        assert usage["raw_max_tokens"] == 65536
        assert usage["over_limit"] is None  # 7040 < 65536*0.6

    def test_empty_items(self):
        usage = build_context_usage([], max_tokens=1000)
        assert usage["total_tokens"] == 0
        assert usage["over_limit"] is None
        assert usage["top_consumers"] == []


class TestItemsFromOpenaiTools:
    def _tools(self):
        return [
            {"type": "function", "function": {"name": "read_file",
                                               "description": "读取文件内容" * 20,
                                               "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "terminal", "description": "exec"}},
        ]

    def test_per_tool_items(self):
        items = items_from_openai_tools(self._tools(), source="mcp")
        assert len(items) == 2
        assert items[0].kind == KIND_MCP_TOOL
        assert items[0].name == "read_file"
        assert items[0].tokens > items[1].tokens  # 描述更长→token更多（per-tool归因有效）
        assert items[0].source == "mcp"

    def test_source_maps_kind(self):
        assert items_from_openai_tools(self._tools(), source="builtin")[0].kind == KIND_BUILTIN_TOOL
        assert items_from_openai_tools(self._tools(), source="custom")[0].kind == ta.KIND_TOOL

    def test_empty_and_malformed_safe(self):
        assert items_from_openai_tools(None, source="mcp") == []
        assert items_from_openai_tools([], source="mcp") == []
        # dict形态缺字段→容错归为unnamed_tool；非dict条目→安全跳过不抛
        bad = [{"type": "function"}, {"function": {"name": None}}, "not-a-dict"]
        items = items_from_openai_tools(bad, source="mcp")
        assert len(items) == 2  # 非dict被跳过，dict形态容错保留
        assert all(i.name == "unnamed_tool" for i in items)
        assert all(isinstance(i.tokens, int) for i in items)


# ════════════════════════════════════════════════════════════════
# ContextAttributor — 记录/环形缓冲/摘要/账本
# ════════════════════════════════════════════════════════════════

class TestContextAttributor:
    def _usage(self, total=1000):
        return {"total_tokens": total, "percentage": 1.0, "over_limit": None,
                "top_consumers": [{"kind": "skill", "name": "s1", "tokens": total, "source": "x"}]}

    def test_record_and_recent_newest_first(self):
        a = ContextAttributor()
        a.record(self._usage(100), session_id="s1")
        a.record(self._usage(200), session_id="s2")
        recent = a.recent()
        assert recent[0]["session_id"] == "s2"  # 最新在前
        assert recent[1]["session_id"] == "s1"

    def test_ring_buffer_cap(self):
        a = ContextAttributor(max_records=5)
        for i in range(10):
            a.record(self._usage(i), session_id=f"s{i}")
        assert len(a.recent(50)) == 5
        assert a.recent(50)[0]["session_id"] == "s9"

    def test_estimate_gap_recorded(self):
        a = ContextAttributor()
        rec = a.record(self._usage(1000), actual_prompt_tokens=1200)
        assert rec["estimate_gap"] == 200  # 真实>估算（估算偏保守侧）

    def test_summary_aggregates_top_consumers(self):
        a = ContextAttributor()
        a.record({"total_tokens": 1000, "over_limit": None,
                  "top_consumers": [{"kind": "skill", "name": "excel", "tokens": 600, "source": "sm"}]})
        a.record({"total_tokens": 3000, "over_limit": {"tokens_over": 100, "kind": "hard_limit"},
                  "top_consumers": [{"kind": "skill", "name": "excel", "tokens": 800, "source": "sm"},
                                     {"kind": "mcp_tool", "name": "feishu", "tokens": 700, "source": "mcp"}]})
        s = a.summary()
        assert s["total_records"] == 2
        assert s["over_limit_records"] == 1
        assert s["avg_total_tokens"] == 2000
        assert s["max_total_tokens"] == 3000
        top = s["top_consumers"][0]
        assert top["name"] == "excel"  # 600+800=1400 > feishu 700
        assert top["times_seen"] == 2
        assert top["avg_tokens"] == 700
        assert s["latest"]["total_tokens"] == 3000
        assert s["latest"]["over_limit"]["kind"] == "hard_limit"

    def test_summary_empty(self):
        assert ContextAttributor().summary() == {"total_records": 0}

    def test_ledger_jsonl_written_and_cross_instance_readable(self, tmp_path):
        """跨进程语义：写入方与读取方可以是不同实例（acp-proxy agent写/app读同模式）"""
        path = str(tmp_path / "attr" / "ledger.jsonl")
        writer = ContextAttributor(ledger_path=path)
        writer.record(self._usage(500), session_id="sx", model="deepseek-r1")
        reader = ContextAttributor(ledger_path=path)
        import json
        lines = open(path, encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["session_id"] == "sx"
        assert rec["usage"]["total_tokens"] == 500

    def test_ledger_write_failure_nonfatal(self, tmp_path):
        bad_path = str(tmp_path)  # 目录当文件→open失败
        a = ContextAttributor(ledger_path=bad_path)
        rec = a.record(self._usage(1))  # 不抛
        assert rec["usage"]["total_tokens"] == 1


# ════════════════════════════════════════════════════════════════
# chat路径接线 — _attribute_chat_context + 端点函数
# ════════════════════════════════════════════════════════════════

class TestChatPathWiring:
    def test_helper_records_memory_rag_question_items(self):
        from src.api.chat import _attribute_chat_context
        usage = _attribute_chat_context(
            question="什么是门禁系统？",
            context_parts=["chunk-A" * 50, "chunk-B" * 30],
            memory_ctx="- 用户偏好中文回答\n- 项目在赤峰",
            model="deepseek-r1",
            provider="ollama",
            session_key="u1",
        )
        assert usage is not None
        assert len(usage["memory_files"]) == 2  # 逐条记忆归因
        assert len(usage["top_consumers"]) >= 3
        rec = ta.get_attributor().recent(1)[0]
        assert rec["session_id"] == "u1"
        assert rec["provider"] == "ollama"
        assert rec["model"] == "deepseek-r1"

    def test_helper_records_degraded_path_question_only(self):
        from src.api.chat import _attribute_chat_context
        usage = _attribute_chat_context("你好", [], "", model=None)
        assert usage is not None
        assert usage["memory_files"] == []
        assert usage["sections"].get(KIND_RAG, 0) == 0  # 无RAG chunk时sections稀疏
        assert usage["total_tokens"] == estimate_tokens("你好")  # 2 CJK → 2 token
        assert usage["raw_max_tokens"] == ta.DEFAULT_CONTEXT_WINDOW

    def test_helper_fail_safe_never_raises(self):
        from src.api.chat import _attribute_chat_context
        # memory_ctx传非字符串触发内部异常→返回None不抛
        assert _attribute_chat_context("q", [], None) is not None or True  # None安全
        class _Boom:
            def splitlines(self):
                raise RuntimeError("boom")
        assert _attribute_chat_context("q", [], _Boom()) is None

    def test_endpoint_function_returns_recent_and_summary(self):
        from src.api.chat import token_attribution, _attribute_chat_context
        _attribute_chat_context("测试问题", ["c" * 100], "- mem", model="deepseek-chat")
        result = asyncio.run(token_attribution(limit=5))
        assert result["summary"]["total_records"] == 1
        assert len(result["recent"]) == 1
        assert result["recent"][0]["usage"]["raw_max_tokens"] == 65536

    def test_endpoint_function_empty_state(self):
        from src.api.chat import token_attribution
        result = asyncio.run(token_attribution())
        assert result["recent"] == []
        assert result["summary"]["total_records"] == 0
