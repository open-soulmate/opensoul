# -*- coding: utf-8 -*-
"""P1 provider usage回填测试 — token归因estimate→真实prompt_tokens校准

调研来源：feature-matrix/10-claude-code-source.md #7 SDKContextUsage（provider权威token计数）；
上轮dev-report遗留#3"estimate_tokens是估算非精确计数，record带actual_prompt_tokens参数+
estimate_gap字段预留自校准，但两侧调用方暂无provider usage回填（opensoul streaming路径拿usage
需要SSE聚合，下轮候选）"——本轮把这条预留容量真正接线：从LLM provider的usage字段捕获真实
prompt_tokens，回填归因记录计算estimate_gap（估算vs真实偏差=我方未归因的chat模板/系统提示开销）。

live provider实证（token-plan xiaomi）：非流式与流式（含无stream_options）均默认返回
usage.prompt_tokens，故捕获零请求格式改动、零provider拒绝风险。

测试分层：单元(backfill_actual/helper/passthrough) + 接线驱动(rag_stream流式两条路径 +
chat非流式，monkeypatch假provider吐usage chunk，断言归因记录被真实回填=非死代码)。
无网络依赖。
"""
import asyncio
import json
import types
import uuid as _uuid

import pytest

import src.api.chat as chat
import src.cortex.token_attribution as ta
from src.cortex.token_attribution import ContextAttributor, reset_attributor


@pytest.fixture(autouse=True)
def _fresh():
    reset_attributor()
    yield
    reset_attributor()


# ────────────────────────────────────────────────────────────────
# 假httpx（AsyncClient）：stream路径吐SSE行，post路径返回JSON usage
# ────────────────────────────────────────────────────────────────

class _FakeStreamResp:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln


class _FakeJsonResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeStreamResp(self._lines)

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, lines=None, json_payload=None):
        self._lines = lines or []
        self._json = json_payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, *a, **k):
        return _FakeStreamCtx(self._lines)

    async def post(self, *a, **k):
        return _FakeJsonResp(self._json)


def _httpx_stub(lines=None, json_payload=None):
    return types.SimpleNamespace(AsyncClient=lambda: _FakeClient(lines, json_payload))


_ATTEMPTS = [{"provider": "online", "base_url": "http://fake",
              "model": "xiaomi/mimo-v2.5-pro", "api_key": "sk-x"}]


def _patch_deps(monkeypatch, results, attempts=None):
    if attempts is None:
        attempts = _ATTEMPTS

    async def _ss(q, uid, limit=5):
        return results

    async def _comp(ctx, budget_chars=12000):
        return ctx

    monkeypatch.setattr(chat, "semantic_search", _ss)
    monkeypatch.setattr(chat, "_compress_if_needed", _comp)
    monkeypatch.setattr(chat, "_get_memory_context", lambda q: "- 用户偏好中文")
    monkeypatch.setattr(chat, "_redact_outbound", lambda p: p)
    monkeypatch.setattr(chat, "_route_llm_targets", lambda q: (attempts, {"mode": "t"}))
    monkeypatch.setattr(chat, "_route_decision_begin", lambda *a, **k: "dec1")
    monkeypatch.setattr(chat, "_route_decision_end", lambda *a, **k: None)
    monkeypatch.setattr(chat, "_check_loop", lambda *a, **k: None)


async def _consume(gen):
    out = []
    async for item in gen:
        out.append(item)
    return out


def _match_record(uid):
    recs = ta.get_attributor().recent(50)
    m = [r for r in recs if r["session_id"] == str(uid)]
    return m[0] if m else None


# ════════════════════════════════════════════════════════════════
# backfill_actual 单元
# ════════════════════════════════════════════════════════════════

class TestBackfillActual:
    def test_annotates_matching_record_and_gap(self):
        a = ContextAttributor()
        a.record({"total_tokens": 100, "top_consumers": []}, session_id="s1")
        rec = a.backfill_actual("s1", 265)
        assert rec is not None
        assert rec["actual_prompt_tokens"] == 265
        assert rec["estimate_gap"] == 265 - 100  # 真实-估算=隐藏开销

    def test_unknown_session_returns_none(self):
        a = ContextAttributor()
        a.record({"total_tokens": 100}, session_id="s1")
        assert a.backfill_actual("nope", 500) is None

    def test_none_input_returns_none(self):
        a = ContextAttributor()
        a.record({"total_tokens": 100}, session_id="s1")
        assert a.backfill_actual("s1", None) is None

    def test_picks_most_recent_matching_session(self):
        a = ContextAttributor()
        a.record({"total_tokens": 100}, session_id="sX")
        a.record({"total_tokens": 900}, session_id="sX")
        rec = a.backfill_actual("sX", 1000)
        assert rec["usage"]["total_tokens"] == 900  # 回填最近一条
        assert rec["estimate_gap"] == 100

    def test_ledger_backfill_line_written(self, tmp_path):
        path = str(tmp_path / "led.jsonl")
        a = ContextAttributor(ledger_path=path)
        a.record({"total_tokens": 100}, session_id="s1")
        a.backfill_actual("s1", 265)
        lines = open(path, encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 2
        bf = json.loads(lines[1])
        assert bf["backfill"] is True
        assert bf["actual_prompt_tokens"] == 265
        assert bf["estimate_gap"] == 265 - 100

    def test_mutates_deque_record_visible_in_recent(self):
        a = ContextAttributor()
        a.record({"total_tokens": 100}, session_id="s1")
        a.backfill_actual("s1", 400)
        assert a.recent(1)[0]["actual_prompt_tokens"] == 400  # API读路径能看到回填


# ════════════════════════════════════════════════════════════════
# chat路径 helper — passthrough + backfill helper
# ════════════════════════════════════════════════════════════════

class TestChatHelperPassthrough:
    def test_attribute_helper_forwards_actual_prompt_tokens(self):
        usage = chat._attribute_chat_context(
            question="你好世界", context_parts=["ctx" * 20], memory_ctx="- mem",
            model="deepseek-r1", provider="ollama", session_key="u9",
            actual_prompt_tokens=777,
        )
        rec = ta.get_attributor().recent(1)[0]
        assert rec["actual_prompt_tokens"] == 777
        assert rec["estimate_gap"] == 777 - usage["total_tokens"]

    def test_attribute_helper_without_actual_no_gap_field(self):
        chat._attribute_chat_context("你好", ["c"], "", session_key="u1")
        rec = ta.get_attributor().recent(1)[0]
        assert rec["actual_prompt_tokens"] is None
        assert "estimate_gap" not in rec  # 未回填时不产生gap

    def test_backfill_helper_extracts_prompt_tokens(self):
        ta.get_attributor().record({"total_tokens": 50}, session_id="s1")
        chat._backfill_token_usage("s1", {"prompt_tokens": 400, "completion_tokens": 5})
        assert ta.get_attributor().recent(1)[0]["actual_prompt_tokens"] == 400

    def test_backfill_helper_fail_safe_on_empty_or_missing(self):
        ta.get_attributor().record({"total_tokens": 50}, session_id="s1")
        chat._backfill_token_usage("s1", None)                       # 空→不改不抛
        chat._backfill_token_usage("s1", {})                         # 无prompt_tokens→不改
        chat._backfill_token_usage("s1", {"completion_tokens": 5})   # 无prompt_tokens→不改
        rec = ta.get_attributor().recent(1)[0]
        assert rec.get("actual_prompt_tokens") is None


# ════════════════════════════════════════════════════════════════
# 接线驱动 — rag_stream流式两路径 + chat非流式（真实调用路径，非死代码）
# ════════════════════════════════════════════════════════════════

class TestStreamingUsageWiring:
    def test_normal_stream_captures_and_backfills_usage(self, monkeypatch):
        uid = _uuid.uuid4()
        _patch_deps(monkeypatch, [{"id": "1", "score": 0.9, "chunk": "门禁系统上下文 " * 10}])
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"ANSWER"}}]}',
            # 真实provider形态：usage伴随空choices数组（触发choices[0]防护）
            'data: {"choices":[],"usage":{"prompt_tokens":255,"completion_tokens":12,"total_tokens":267}}',
            "data: [DONE]",
        ]
        monkeypatch.setattr(chat, "httpx", _httpx_stub(lines=sse_lines))
        out = asyncio.run(_consume(chat.rag_stream("什么是门禁？", uid, 5)))
        assert any("ANSWER" in (o or "") for o in out)  # 内容确实流出来了
        rec = _match_record(uid)
        assert rec is not None, "归因记录未生成"
        assert rec["actual_prompt_tokens"] == 255
        assert rec["estimate_gap"] == 255 - rec["usage"]["total_tokens"]

    def test_degraded_stream_backfills_usage(self, monkeypatch):
        uid = _uuid.uuid4()
        _patch_deps(monkeypatch, [])  # semantic_search返回[]→降级路径
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"DIRECT"}}]}',
            'data: {"choices":[],"usage":{"prompt_tokens":120,"completion_tokens":8,"total_tokens":128}}',
            "data: [DONE]",
        ]
        monkeypatch.setattr(chat, "httpx", _httpx_stub(lines=sse_lines))
        out = asyncio.run(_consume(chat.rag_stream("你好", uid, 5)))
        assert any("DIRECT" in (o or "") for o in out)
        rec = _match_record(uid)
        assert rec is not None
        assert rec["actual_prompt_tokens"] == 120
        assert rec["estimate_gap"] == 120 - rec["usage"]["total_tokens"]

    def test_stream_survives_empty_choices_usage_chunk(self, monkeypatch):
        """真实provider末尾{\"choices\":[],...}chunk：旧代码choices[0]会IndexError炸流；
        防护后流不中断、能跑到[DONE]且usage仍被捕获回填。"""
        uid = _uuid.uuid4()
        _patch_deps(monkeypatch, [{"id": "1", "score": 0.9, "chunk": "ctx " * 20}])
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"OK"}}]}',
            'data: {"choices":[],"usage":{"prompt_tokens":99,"completion_tokens":3,"total_tokens":102}}',
            "data: [DONE]",
        ]
        monkeypatch.setattr(chat, "httpx", _httpx_stub(lines=sse_lines))
        out = asyncio.run(_consume(chat.rag_stream("q", uid, 5)))  # 不抛IndexError
        # 流走到了收尾（含[DONE]），且usage被回填
        assert any("[DONE]" in (o or "") for o in out)
        rec = _match_record(uid)
        assert rec is not None
        assert rec["actual_prompt_tokens"] == 99
        assert rec["estimate_gap"] == 99 - rec["usage"]["total_tokens"]

    def test_stream_without_usage_chunk_leaves_estimate_only(self, monkeypatch):
        """provider不吐usage（本地ollama等）→ 记录保持估算、无gap，不误填。"""
        uid = _uuid.uuid4()
        _patch_deps(monkeypatch, [{"id": "1", "score": 0.9, "chunk": "ctx " * 20}])
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"答案"}}]}',
            "data: [DONE]",
        ]
        monkeypatch.setattr(chat, "httpx", _httpx_stub(lines=sse_lines))
        asyncio.run(_consume(chat.rag_stream("问题", uid, 5)))
        rec = _match_record(uid)
        assert rec is not None
        assert rec.get("actual_prompt_tokens") is None
        assert "estimate_gap" not in rec


class TestNonStreamUsageWiring:
    def test_nonstream_passes_actual_prompt_tokens(self, monkeypatch):
        uid = _uuid.uuid4()
        _patch_deps(monkeypatch, [{"id": "1", "score": 0.9, "chunk": "上下文 " * 10}])
        payload = {
            "choices": [{"message": {"content": "回答"}}],
            "usage": {"prompt_tokens": 310, "completion_tokens": 20, "total_tokens": 330},
        }
        monkeypatch.setattr(chat, "httpx", _httpx_stub(json_payload=payload))
        req = chat.ChatRequest(question="问题", top_k=5, stream=False)
        result = asyncio.run(chat.chat(req, uid))
        assert result["answer"] == "回答"
        rec = _match_record(uid)
        assert rec is not None
        assert rec["actual_prompt_tokens"] == 310
        assert rec["estimate_gap"] == 310 - rec["usage"]["total_tokens"]

    def test_nonstream_missing_usage_no_gap(self, monkeypatch):
        uid = _uuid.uuid4()
        _patch_deps(monkeypatch, [{"id": "1", "score": 0.9, "chunk": "c " * 10}])
        payload = {"choices": [{"message": {"content": "回答"}}]}  # 无usage
        monkeypatch.setattr(chat, "httpx", _httpx_stub(json_payload=payload))
        req = chat.ChatRequest(question="q", top_k=5, stream=False)
        asyncio.run(chat.chat(req, uid))
        rec = _match_record(uid)
        assert rec is not None
        assert rec.get("actual_prompt_tokens") is None
        assert "estimate_gap" not in rec


# ════════════════════════════════════════════════════════════════
# 端点读路径 — 回填后的字段通过 /api/chat/token-attribution 可见
# ════════════════════════════════════════════════════════════════

class TestEndpointExposesBackfill:
    def test_endpoint_recent_contains_actual_and_gap(self):
        chat._attribute_chat_context("测试", ["c" * 100], "- mem",
                                     model="deepseek-chat", session_key="uE",
                                     actual_prompt_tokens=500)
        result = asyncio.run(chat.token_attribution(limit=5))
        rec = result["recent"][0]
        assert rec["actual_prompt_tokens"] == 500
        assert rec["estimate_gap"] == 500 - rec["usage"]["total_tokens"]
