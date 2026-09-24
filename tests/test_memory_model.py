"""kilocode supplement3 #8 记忆模型独立解析链（MemoryModel.port）测试。

覆盖：
- spec解析（invalid model与"未配置"严格区分——失败必须可见）
- 解析链四分支：未配置→会话模型 / 非法→warn回退 / 不可用→warn回退 / 有效→记忆模型
- call_memory_llm：timeout+调用方取消双闸（AbortSignal.any同构）、完成优先、
  temperature/topP/topK按模型解析、记忆模型故障一次性回退会话模型、失败可见
- dream_distiller / memory_pipeline 委托接线（真实调用路径，防死接线）
- gland router top_p/top_k透传payload字节恒等（None时不进payload）
"""

import asyncio
import inspect
import json
import os
import sys
import time

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.hippo import memory_model
from src.hippo.memory_model import (
    DEFAULT_MEMORY_TIMEOUT_S,
    ResolvedMemoryModel,
    call_memory_llm,
    parse_memory_model_spec,
    resolve_memory_model,
)


class FakeRouter:
    """记录router.chat调用参数的测试桩（响应为OpenAI风格响应体）。"""

    def __init__(self, content: str = "ok", fail: Exception | None = None, delay: float = 0.0):
        self.content = content
        self.fail = fail
        self.delay = delay
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        return {"choices": [{"message": {"content": self.content}}]}


def _resolved(model="mem-mini", source="memory_config", fallback=None, sampling=None):
    return ResolvedMemoryModel(
        model_id=model,
        base_url="https://mem.example/v1",
        api_key="k",
        source=source,
        fallback=fallback,
        sampling=dict(sampling or {}),
    )


# ── spec解析 ─────────────────────────────────────────────────


class TestSpecParse:
    def test_plain_model_id(self):
        assert parse_memory_model_spec("gpt-4o-mini") == "gpt-4o-mini"

    def test_provider_prefixed_model(self):
        assert parse_memory_model_spec("xiaomi/mimo-v2.5") == "xiaomi/mimo-v2.5"

    def test_url_like_model(self):
        assert parse_memory_model_spec("org/model:v1.2") == "org/model:v1.2"

    def test_invalid_internal_whitespace(self):
        assert parse_memory_model_spec("gpt 4o") is None

    def test_invalid_control_chars(self):
        assert parse_memory_model_spec("gpt\n4o") is None

    def test_invalid_too_long(self):
        assert parse_memory_model_spec("m" * 300) is None

    def test_invalid_leading_punct(self):
        assert parse_memory_model_spec("-bad") is None

    def test_empty_is_none(self):
        assert parse_memory_model_spec("") is None
        assert parse_memory_model_spec("   ") is None


# ── 解析链（kilocode MemoryModel.port.resolve四分支）──────────


class TestResolve:
    def test_unconfigured_uses_session_model(self):
        res = resolve_memory_model(
            configured="",
            session_model="mimo-pro",
            session_base_url="https://s/v1",
            session_api_key="sk",
        )
        assert res.model_id == "mimo-pro"
        assert res.source == "session"
        assert res.fallback is None  # 未配置≠失败，不打fallback

    def test_invalid_spec_warns_and_falls_back(self, caplog):
        with caplog.at_level("WARNING", logger="opensoul.hippo.memory_model"):
            res = resolve_memory_model(
                configured="bad model!",
                session_model="mimo-pro",
                session_base_url="https://s/v1",
                session_api_key="sk",
            )
        assert res.model_id == "mimo-pro"
        assert res.source == "session"
        assert res.fallback == {"reason": "invalid model", "configured": "bad model!"}
        assert any("invalid model" in r.message for r in caplog.records)

    def test_unavailable_model_warns_and_falls_back(self, caplog):
        with caplog.at_level("WARNING", logger="opensoul.hippo.memory_model"):
            res = resolve_memory_model(
                configured="ghost-model",
                session_model="mimo-pro",
                session_base_url="https://s/v1",
                session_api_key="sk",
                model_exists=lambda m: False,
            )
        assert res.model_id == "mimo-pro"
        assert res.fallback == {"reason": "model unavailable", "configured": "ghost-model"}
        assert any("model unavailable" in r.message for r in caplog.records)

    def test_available_model_wins(self):
        seen = []
        res = resolve_memory_model(
            configured="mem-mini",
            session_model="mimo-pro",
            session_base_url="https://s/v1",
            session_api_key="sk",
            memory_base_url="https://m/v1",
            memory_api_key="mk",
            model_exists=lambda m: seen.append(m) or True,
        )
        assert res.model_id == "mem-mini"
        assert res.source == "memory_config"
        assert res.fallback is None
        assert res.base_url == "https://m/v1"
        assert res.api_key == "mk"
        assert seen == ["mem-mini"]

    def test_memory_base_url_defaults_to_session(self):
        res = resolve_memory_model(
            configured="mem-mini",
            session_model="mimo-pro",
            session_base_url="https://s/v1",
            session_api_key="sk",
            memory_base_url="",
            memory_api_key="",
        )
        assert res.base_url == "https://s/v1"
        assert res.api_key == "sk"

    def test_sampling_passthrough(self):
        res = resolve_memory_model(
            configured="mem-mini",
            session_model="m",
            session_base_url="u",
            session_api_key="k",
            memory_base_url="mu",
            memory_api_key="mk",
            sampling={"temperature": 0.1, "top_p": 0.9, "top_k": 40},
        )
        assert res.sampling == {"temperature": 0.1, "top_p": 0.9, "top_k": 40}

    def test_describe_shape(self):
        res = _resolved(sampling={"temperature": 0.1, "top_p": None})
        d = res.describe()
        assert d["resolved_model"] == "mem-mini"
        assert d["source"] == "memory_config"
        # None采样键不进快照
        assert d["sampling"] == {"temperature": 0.1}


# ── call_memory_llm：双取消 + 采样解析 + 运行时回退 ───────────


class TestCallMemoryLlm:
    def test_success_and_explicit_model_wins(self):
        fake = FakeRouter(content="hello")
        out = asyncio.run(
            call_memory_llm(
                "sys",
                "usr",
                temperature=0.3,
                resolved=_resolved(),
                router_factory=lambda r: fake,
            )
        )
        assert out == "hello"  # extract_chat_text权威解包
        kw = fake.calls[0]
        assert kw["model"] == "mem-mini"  # 显式model恒赢（独立解析链不许被role路由顶掉）
        assert kw["temperature"] == 0.3
        assert kw["max_tokens"] == 4096
        assert kw["role"] == "summarize"
        assert kw["messages"][0] == {"role": "system", "content": "sys"}

    def test_sampling_per_model_overrides_caller(self):
        fake = FakeRouter()
        asyncio.run(
            call_memory_llm(
                "s",
                "u",
                temperature=0.9,
                resolved=_resolved(sampling={"temperature": 0.1, "top_p": 0.9, "top_k": 40}),
                router_factory=lambda r: fake,
            )
        )
        kw = fake.calls[0]
        assert kw["temperature"] == 0.1  # 按模型解析的采样赢调用方默认
        assert kw["top_p"] == 0.9
        assert kw["top_k"] == 40

    def test_no_sampling_keeps_none(self):
        fake = FakeRouter()
        asyncio.run(call_memory_llm("s", "u", resolved=_resolved(), router_factory=lambda r: fake))
        kw = fake.calls[0]
        assert kw["top_p"] is None and kw["top_k"] is None

    def test_timeout_aborts_inflight(self):
        fake = FakeRouter(delay=5.0)
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            asyncio.run(
                call_memory_llm(
                    "s",
                    "u",
                    timeout_s=0.3,
                    resolved=_resolved(),
                    router_factory=lambda r: fake,
                )
            )
        assert time.monotonic() - t0 < 3.0  # 超时即中止，不等满5s

    def test_cancel_aborts_inflight(self):
        fake = FakeRouter(delay=5.0)
        ev = asyncio.Event()

        async def _run():
            loop = asyncio.get_event_loop()
            loop.call_later(0.2, ev.set)
            await call_memory_llm(
                "s",
                "u",
                timeout_s=30,
                cancel_event=ev,
                resolved=_resolved(),
                router_factory=lambda r: fake,
            )

        t0 = time.monotonic()
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(_run())
        assert time.monotonic() - t0 < 3.0

    def test_precancel_never_calls_router(self):
        fake = FakeRouter()
        ev = asyncio.Event()
        ev.set()
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                call_memory_llm(
                    "s",
                    "u",
                    cancel_event=ev,
                    resolved=_resolved(),
                    router_factory=lambda r: fake,
                )
            )
        assert fake.calls == []

    def test_completion_wins_over_pending_timeout(self):
        fake = FakeRouter(content="fast")
        out = asyncio.run(
            call_memory_llm(
                "s",
                "u",
                timeout_s=30,
                resolved=_resolved(),
                router_factory=lambda r: fake,
            )
        )
        assert out == "fast"

    def test_session_model_failure_is_visible(self):
        fake = FakeRouter(fail=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            asyncio.run(
                call_memory_llm(
                    "s",
                    "u",
                    resolved=_resolved(source="session"),
                    router_factory=lambda r: fake,
                )
            )

    def test_memory_model_failure_falls_back_to_session(self, caplog):
        calls = []

        def factory(res):
            calls.append(res.model_id)
            return FakeRouter(
                content=f"from-{res.model_id}",
                fail=None if res.source == "session" else RuntimeError("mem down"),
            )

        with caplog.at_level("WARNING", logger="opensoul.hippo.memory_model"):
            out = asyncio.run(
                call_memory_llm(
                    "s",
                    "u",
                    resolved=resolve_memory_model(
                        configured="mem-mini",
                        session_model="mimo-pro",
                        session_base_url="https://s/v1",
                        session_api_key="sk",
                        memory_base_url="https://m/v1",
                        memory_api_key="mk",
                    ),
                    router_factory=factory,
                )
            )
        assert out == "from-mimo-pro"  # "失败回退会话模型"
        assert calls == ["mem-mini", "mimo-pro"]
        assert any("falling back to session model" in r.message for r in caplog.records)
        assert memory_model.describe()["runtime_fallback_count"] >= 1

    def test_both_models_fail_propagates(self):
        def factory(res):
            return FakeRouter(fail=RuntimeError(f"down-{res.model_id}"))

        with pytest.raises(RuntimeError, match="down-mimo-pro"):
            asyncio.run(
                call_memory_llm(
                    "s",
                    "u",
                    resolved=resolve_memory_model(
                        configured="mem-mini",
                        session_model="mimo-pro",
                        session_base_url="https://s/v1",
                        session_api_key="sk",
                        memory_base_url="https://m/v1",
                        memory_api_key="mk",
                    ),
                    router_factory=factory,
                )
            )

    def test_timeout_covers_fallback_chain(self):
        """回退重试共享同一超时预算（AbortSignal.any=总预算，不是每次尝试各一份）。"""
        fake = FakeRouter(delay=5.0)
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            asyncio.run(
                call_memory_llm(
                    "s",
                    "u",
                    timeout_s=0.3,
                    resolved=resolve_memory_model(
                        configured="mem-mini",
                        session_model="mimo-pro",
                        session_base_url="https://s/v1",
                        session_api_key="sk",
                        memory_base_url="https://m/v1",
                        memory_api_key="mk",
                    ),
                    router_factory=lambda r: (
                        FakeRouter(fail=RuntimeError("x")) if r.source == "memory_config" else fake
                    ),
                )
            )
        assert time.monotonic() - t0 < 3.0


# ── dream / pipeline 委托接线（真实调用路径）──────────────────


class TestDreamPipelineDelegation:
    def test_dream_delegates_with_03(self, monkeypatch):
        from src.hippo import dream_distiller

        seen = {}

        async def fake_call(system_prompt, user_prompt, **kw):
            seen.update(kw)
            return "[]"

        monkeypatch.setattr(dream_distiller, "call_memory_llm", fake_call)
        d = dream_distiller.DreamDistiller(ltm_store=_store(), llm_call=None)
        out = asyncio.run(d._call_gland_llm("s", "u"))
        assert out == "[]"
        assert seen.get("temperature") == 0.3  # dream蒸馏稳定复现采样

    def test_dream_full_path_through_new_chain(self, monkeypatch):
        from src.hippo import dream_distiller

        async def fake_call(system_prompt, user_prompt, **kw):
            return '[{"action": "ADD", "content": "probe fact", "memory_type": "semantic", "importance": 0.5, "reason": "r", "tags": []}]'

        monkeypatch.setattr(dream_distiller, "call_memory_llm", fake_call)
        d, store = _distiller()
        result = asyncio.run(
            d.dream(
                [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
                force=True,
            )
        )
        assert result.applied >= 1  # 真实执行链（解析→执行→落库）经新解析链走通
        assert any("probe fact" in (m.get("content") or "") for m in store.list_memories())

    def test_dream_timeout_not_wrapped(self, monkeypatch):
        from src.hippo import dream_distiller

        async def fake_call(system_prompt, user_prompt, **kw):
            raise TimeoutError("memory model timed out after 0.3s")

        monkeypatch.setattr(dream_distiller, "call_memory_llm", fake_call)
        d = dream_distiller.DreamDistiller(ltm_store=_store(), llm_call=None)
        with pytest.raises(TimeoutError):
            asyncio.run(d._call_gland_llm("s", "u"))  # 显式契约不伪装成router故障

    def test_dream_other_error_keeps_contract(self, monkeypatch):
        from src.hippo import dream_distiller

        async def fake_call(system_prompt, user_prompt, **kw):
            raise KeyError("nope")

        monkeypatch.setattr(dream_distiller, "call_memory_llm", fake_call)
        d = dream_distiller.DreamDistiller(ltm_store=_store(), llm_call=None)
        with pytest.raises(RuntimeError, match="Gland router call failed"):
            asyncio.run(d._call_gland_llm("s", "u"))

    def test_pipeline_delegates_with_02(self, monkeypatch):
        from src.hippo import memory_pipeline

        seen = {}

        async def fake_call(system_prompt, user_prompt, **kw):
            seen.update(kw)
            return "[]"

        monkeypatch.setattr(memory_pipeline, "call_memory_llm", fake_call)
        p = memory_pipeline.MemoryPipeline(ltm_store=_lt(), llm_call=None)
        asyncio.run(p._call_llm("s", "u"))
        assert seen.get("temperature") == 0.2  # 整合决策稳定复现采样

    def test_pipeline_explicit_llm_call_wins(self, monkeypatch):
        from src.hippo import memory_pipeline

        async def never(*a, **kw):  # pragma: no cover - 断言不被调用
            raise AssertionError("call_memory_llm must not run when llm_call is set")

        async def explicit(system_prompt, user_prompt):
            return "explicit"

        monkeypatch.setattr(memory_pipeline, "call_memory_llm", never)
        p = memory_pipeline.MemoryPipeline(ltm_store=_lt(), llm_call=explicit)
        assert asyncio.run(p._call_llm("s", "u")) == "explicit"


# ── Wiring（防死接线）─────────────────────────────────────────


class TestWiring:
    def test_dream_body_delegates(self):
        from src.hippo.dream_distiller import DreamDistiller

        src_text = inspect.getsource(DreamDistiller._call_gland_llm)
        assert "call_memory_llm" in src_text
        assert "add_provider" not in src_text  # 手搓router注册块绝迹

    def test_pipeline_body_delegates(self):
        from src.hippo.memory_pipeline import MemoryPipeline

        src_text = inspect.getsource(MemoryPipeline._call_llm)
        assert "call_memory_llm" in src_text
        assert "add_provider" not in src_text

    def test_health_carries_memory_model(self):
        from src.api import hippo as hippo_api

        src_text = inspect.getsource(hippo_api.health)
        assert "_memory_model.describe()" in src_text

    def test_router_payload_byte_identical_without_sampling(self):
        from src.gland.router import _chat_payload

        legacy = {
            "model": "m",
            "messages": [{"role": "user", "content": "x"}],
            "temperature": 0.5,
            "max_tokens": 10,
            "stream": False,
        }
        assert _chat_payload("m", [{"role": "user", "content": "x"}], 0.5, 10, False) == legacy
        with_sampling = _chat_payload("m", [], 0.5, 10, False, top_p=0.9, top_k=40)
        assert with_sampling["top_p"] == 0.9 and with_sampling["top_k"] == 40

    def test_settings_knobs_exist(self):
        from src.config import settings

        assert hasattr(settings, "memory_model")
        assert hasattr(settings, "memory_llm_timeout_s")
        assert DEFAULT_MEMORY_TIMEOUT_S == 120.0

    def test_describe_fail_safe(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("broken settings")

        monkeypatch.setattr(memory_model, "resolve_memory_model", boom)
        out = memory_model.describe()
        assert "error" in out


# ── helpers ──────────────────────────────────────────────────


def _store():
    import tempfile

    from src.hippo.long_term_memory import LongTermMemoryStore

    return LongTermMemoryStore(db_path=tempfile.mktemp(suffix=".db"))


def _lt():
    return _store()


def _distiller():
    from src.hippo.dream_distiller import DreamDistiller

    store = _store()
    return DreamDistiller(ltm_store=store, llm_call=None), store


# ── 变体备胎入链 + live失败形态回归（2026-09-25轮） ─────────────────
# 根因链：显式model劫持备胎link（`ollama/mimo-v2.5-pro`实录）+ 真实备胎
# （标准API/订阅制双体系的另一变体）从未入链 + LLM_SUBSCRIPTION_MODEL=
# partial-test占位符 → dream/Phase1 全链饿死（"All providers failed after
# 4 chain attempt(s)"，journalctl 2026-09-24 23:00实录）。此处锁死修复后行为。

_OVERRIDES_ACTIVE_STD = {
    "active_variant": "standard",
    "base_url": "https://mem.example/v1",
    "api_key": "sk-std",
    "model": "m-v",
    "standard_base_url": "https://mem.example/v1",
    "standard_api_key": "sk-std",
    "standard_model": "m-v",
    "subscription_base_url": "https://sub.example/v1",
    "subscription_api_key": "tp-sub",
    "subscription_model": "partial-test",
}


class TestVariantBackupWiring:
    def test_build_router_registers_variant_backup(self, monkeypatch):
        monkeypatch.setattr("src.api.llm._llm_overrides", dict(_OVERRIDES_ACTIVE_STD))
        r = memory_model._build_router(_resolved(model="m-v"))
        assert "variant-subscription" in r.providers
        vp = r.providers["variant-subscription"]
        assert vp.priority == 5
        assert vp.models == {"chat": "partial-test"}
        assert r.key_manager.next_key("variant-subscription") == "tp-sub"

    def test_no_backup_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(
            "src.api.llm._llm_overrides",
            {"active_variant": "standard", "base_url": "https://mem.example/v1"},
        )
        r = memory_model._build_router(_resolved())
        assert not any(n.startswith("variant-") for n in r.providers)

    def test_no_backup_when_same_endpoint(self, monkeypatch):
        ov = dict(_OVERRIDES_ACTIVE_STD)
        ov["subscription_base_url"] = "https://mem.example/v1"  # 同端点=非备胎
        monkeypatch.setattr("src.api.llm._llm_overrides", ov)
        r = memory_model._build_router(_resolved())
        assert not any(n.startswith("variant-") for n in r.providers)

    def test_backup_model_falls_back_to_resolved_when_empty(self, monkeypatch):
        ov = dict(_OVERRIDES_ACTIVE_STD)
        ov["subscription_model"] = ""
        monkeypatch.setattr("src.api.llm._llm_overrides", ov)
        r = memory_model._build_router(_resolved(model="m-v"))
        assert r.providers["variant-subscription"].models == {"chat": "m-v"}

    def test_alternate_variant_flips_with_active(self, monkeypatch):
        from src.api.llm import alternate_variant_config

        # 激活subscription时备胎=standard（生效槽位=激活变体的值）
        ov = dict(_OVERRIDES_ACTIVE_STD)
        ov.update(
            {
                "active_variant": "subscription",
                "base_url": "https://sub.example/v1",
                "api_key": "tp-sub",
                "model": "partial-test",
            }
        )
        monkeypatch.setattr("src.api.llm._llm_overrides", ov)
        alt = alternate_variant_config()
        assert alt is not None
        assert alt["variant"] == "standard"
        assert alt["base_url"] == "https://mem.example/v1"
        assert alt["model"] == "m-v"

    @pytest.mark.asyncio
    async def test_call_memory_llm_survives_primary_402_via_backup(self, monkeypatch):
        """live失败形态全回归：primary 402 + 备胎占位模型400 → 显式model接住。

        修复前同样输入=AllProvidersFailedError（4链attempt全灭实录）；
        修复后经变体备胎成活，且tp- key走api-key头、显式model不劫持备胎。
        """
        monkeypatch.setattr("src.api.llm._llm_overrides", dict(_OVERRIDES_ACTIVE_STD))
        # 路由模式钉死：ollama是localhost provider，cost模式会把它排到最前
        monkeypatch.setattr("src.gland.route_policy.get_mode", lambda: "balance")
        seen: list[tuple] = []

        def handler(request: httpx.Request) -> httpx.Response:
            model = json.loads(request.content)["model"]
            seen.append(
                (
                    request.url.host,
                    model,
                    request.headers.get("api-key"),
                    request.headers.get("authorization"),
                )
            )
            if request.url.host == "mem.example":
                return httpx.Response(402, json={"error": "quota"})
            if request.url.host == "sub.example":
                if model == "partial-test":
                    return httpx.Response(400, json={"error": "Unsupported model partial-test"})
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "memory-out"}}], "usage": {}},
                )
            return httpx.Response(500, json={"error": "unexpected host"})

        def factory(res):
            router = memory_model._build_router(res)
            router._http_client = httpx.AsyncClient(
                transport=httpx.MockTransport(handler), timeout=5
            )
            return router

        res = ResolvedMemoryModel(
            model_id="m-v",
            base_url="https://mem.example/v1",
            api_key="sk-std",
            source="session",
            sampling={},
        )
        out = await call_memory_llm("sys", "usr", resolved=res, router_factory=factory)
        assert out == "memory-out"
        assert seen == [
            ("mem.example", "m-v", None, "Bearer sk-std"),
            ("sub.example", "partial-test", "tp-sub", None),
            ("sub.example", "m-v", "tp-sub", None),
        ]
