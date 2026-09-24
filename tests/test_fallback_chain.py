"""CowAgent ordered fallback-chain tests for the gland ModelRouter.

Research source: 38-CowAgent-source-supplement3.md #12 (protocol/agent_stream.py,
commit-as-spec, 68 tests upstream) + SUMMARY.md cortex P0
"cortex | 模型降级有序链（fallback+限流立即切备胎）| CowAgent chat fallback链 | P0".

Behaviors under test:
  1. Ordered [{provider, model}] chain — priority decides link order.
  2. Rate-limit fast-switch: 429 + backup link exists → switch immediately,
     no in-place retry ("限流有备胎立即切换").
  3. Rate-limit last link: no backup → retry in place ("没备胎干等").
  4. Wrap-around pass 2: transient-error exhaustion + ≥2 links → one fast
     re-probe per link ("瞬时限流已恢复不该废掉整个turn").
  5. No wrap-around for permanent errors or single-link chains.
  6. Exhaustion error lists every tried {provider, model, pass, error}.
  7. Chain trace recorded + exposed for observability.
  8. Keyless providers (local Ollama) stay in the chain; no auth header sent.

Runs without a live server — HTTP mocked with httpx.MockTransport. All error
responses carry retry-after-ms: 1 so any in-place retry sleeps ~1ms.
"""

import json

import httpx
import pytest

from src.gland.router import (
    AllProvidersFailedError,
    ModelRouter,
    NoProviderError,
    TaskType,
    _auth_headers,
    _is_model_rejected,
    _is_rate_limited,
    _is_transient_error,
    _model_candidates,
)

CHAT_OK = httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})


def _resp(status: int, content: str = "ok") -> httpx.Response:
    if status == 200:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {}},
        )
    return httpx.Response(status, headers={"retry-after-ms": "1"}, json={"error": f"e{status}"})


class TestErrorClassification:
    def test_429_is_rate_limited(self):
        req = httpx.Request("POST", "http://x/c")
        resp = httpx.Response(429, request=req)
        exc = httpx.HTTPStatusError("rl", request=req, response=resp)
        assert _is_rate_limited(exc)

    def test_500_not_rate_limited(self):
        req = httpx.Request("POST", "http://x/c")
        resp = httpx.Response(500, request=req)
        exc = httpx.HTTPStatusError("err", request=req, response=resp)
        assert not _is_rate_limited(exc)

    def test_transient_429_5xx_transport(self):
        req = httpx.Request("POST", "http://x/c")
        for status in (429, 500, 503):
            resp = httpx.Response(status, request=req)
            exc = httpx.HTTPStatusError("e", request=req, response=resp)
            assert _is_transient_error(exc), status
        assert _is_transient_error(httpx.ConnectError("refused"))

    def test_permanent_errors_not_transient(self):
        req = httpx.Request("POST", "http://x/c")
        for status in (400, 401, 404):
            resp = httpx.Response(status, request=req)
            exc = httpx.HTTPStatusError("e", request=req, response=resp)
            assert not _is_transient_error(exc), status
        assert not _is_transient_error(ValueError("boom"))


class ChainTestBase:
    def _make_router(self, handler, providers: list[tuple]) -> ModelRouter:
        """providers: list of (name, base_url, models_dict, priority[, add_key])."""
        router = ModelRouter()
        router._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)
        for spec in providers:
            name, base_url, models, priority = spec[:4]
            add_key = spec[4] if len(spec) > 4 else True
            router.add_provider(name, base_url, models=models, priority=priority)
            if add_key:
                router.key_manager.add_key(name, f"key-{name}")
        return router

    def _two_provider_router(self, handler, a_models=None, b_models=None):
        return self._make_router(
            handler,
            [
                ("a", "http://mock-a", a_models or {"chat": "ma", "embedding": "ea"}, 0),
                ("b", "http://mock-b", b_models or {"chat": "mb", "embedding": "eb"}, 1),
            ],
        )


class TestChainConstruction(ChainTestBase):
    def test_links_ordered_by_priority(self):
        router = self._make_router(
            lambda r: CHAT_OK,
            [
                ("low", "http://low", {"chat": "m1"}, 10),
                ("high", "http://high", {"chat": "m2"}, 0),
            ],
        )
        candidates = router._candidate_providers(TaskType.CHAT)
        links = router._build_links(candidates, TaskType.CHAT, None)
        assert [p.name for p, _, _ in links] == ["high", "low"]
        assert links[0][1] == ("m2",)  # per-link model candidates (备胎降级语义)

    def test_keyless_provider_stays_in_chain(self):
        """Ollama-like provider without API keys must not be silently dropped."""
        router = self._make_router(
            lambda r: CHAT_OK,
            [("ollama", "http://local", {"chat": "llama3.2"}, 10, False)],
        )
        candidates = router._candidate_providers(TaskType.CHAT)
        links = router._build_links(candidates, TaskType.CHAT, None)
        assert len(links) == 1
        assert links[0][2] == ""  # empty key, still in chain

    def test_provider_without_task_model_excluded(self):
        router = self._make_router(
            lambda r: CHAT_OK,
            [
                ("vision-only", "http://v", {"vision": "gpt-4v"}, 0),
                ("chatty", "http://c", {"chat": "m"}, 1),
            ],
        )
        candidates = router._candidate_providers(TaskType.CHAT)
        links = router._build_links(candidates, TaskType.CHAT, None)
        assert [p.name for p, _, _ in links] == ["chatty"]

    @pytest.mark.asyncio
    async def test_no_candidates_raises(self):
        router = self._make_router(lambda r: CHAT_OK, [])
        with pytest.raises(NoProviderError):
            await router.chat([{"role": "user", "content": "hi"}])
        await router.shutdown()


class TestRateLimitFastSwitch(ChainTestBase):
    @pytest.mark.asyncio
    async def test_rate_limit_with_backup_switches_immediately(self):
        """429 on link A + backup B exists → A must be called exactly once."""
        calls = {"a": 0, "b": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "mock-a":
                calls["a"] += 1
                return _resp(429)
            calls["b"] += 1
            return _resp(200, "from-b")

        router = self._two_provider_router(handler)
        try:
            result = await router.chat([{"role": "user", "content": "hi"}])
            assert result["choices"][0]["message"]["content"] == "from-b"
            assert calls["a"] == 1  # fast-switch: zero retries on rate-limited A
            assert calls["b"] == 1
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_rate_limit_last_link_waits_in_place(self):
        """No backup ("没备胎干等"): the last link retries in place."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] <= 2:
                return _resp(429)
            return _resp(200, "recovered")

        router = self._make_router(handler, [("solo", "http://mock", {"chat": "m"}, 0)])
        try:
            result = await router.chat([{"role": "user", "content": "hi"}])
            assert result["choices"][0]["message"]["content"] == "recovered"
            assert calls["n"] == 3  # 429, 429 retry, success
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_fast_switch_failure_mark_bounded(self):
        """Fast-switched provider gets one failure mark, not a cooldown."""
        calls = {"a": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "mock-a":
                calls["a"] += 1
                return _resp(429)
            return _resp(200)

        router = self._two_provider_router(handler)
        try:
            await router.chat([{"role": "user", "content": "hi"}])
            pa = router.providers["a"]
            assert pa._consecutive_failures == 1
            assert pa._cooldown_until == 0.0  # below MAX_FAILURES=3
        finally:
            await router.shutdown()


class TestWrapAround(ChainTestBase):
    @pytest.mark.asyncio
    async def test_wraparound_recovers_on_second_pass(self):
        """A always 429; B 429 in pass 1 then healthy → pass 2 probe succeeds.

        Call accounting:
          pass 1: A=1 (fast-switch), B=3 (last link, in-place retry ×3)
          pass 2: A=1 (re-probe), B=1 (re-probe → 200)
        """
        calls = {"a": 0, "b": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "mock-a":
                calls["a"] += 1
                return _resp(429)
            calls["b"] += 1
            if calls["b"] <= 3:
                return _resp(429)
            return _resp(200, "pass2-win")

        router = self._two_provider_router(handler)
        try:
            result = await router.chat([{"role": "user", "content": "hi"}])
            assert result["choices"][0]["message"]["content"] == "pass2-win"
            assert calls["a"] == 2
            assert calls["b"] == 4
            trace = router.get_chain_trace()
            assert trace[-1]["outcome"] == "success"
            assert trace[-1]["pass"] == 2
            assert trace[-1]["provider"] == "b"
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_no_wraparound_for_permanent_errors(self):
        """401/404 will not fix themselves — no second pass, calls stay minimal."""
        calls = {"a": 0, "b": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "mock-a":
                calls["a"] += 1
                return _resp(401)
            calls["b"] += 1
            return _resp(404)

        router = self._two_provider_router(handler)
        try:
            with pytest.raises(AllProvidersFailedError):
                await router.chat([{"role": "user", "content": "hi"}])
            assert calls["a"] == 1
            assert calls["b"] == 1  # non-retryable + no wrap-around
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_no_wraparound_single_provider(self):
        """Single-link chain: retry already covers transient errors; no pass 2."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return _resp(500)

        router = self._make_router(handler, [("solo", "http://mock", {"chat": "m"}, 0)])
        router.RETRY_MAX_ATTEMPTS = 2
        try:
            with pytest.raises(AllProvidersFailedError):
                await router.chat([{"role": "user", "content": "hi"}])
            assert calls["n"] == 2  # exactly RETRY_MAX_ATTEMPTS, no extra pass
        finally:
            await router.shutdown()


class TestExhaustionError(ChainTestBase):
    @pytest.mark.asyncio
    async def test_error_lists_all_tried_models(self):
        """CowAgent: 链耗尽时报错列出所有试过的模型."""

        def handler(request: httpx.Request) -> httpx.Response:
            return _resp(500) if request.url.host == "mock-a" else _resp(404)

        router = self._two_provider_router(handler)
        router.RETRY_MAX_ATTEMPTS = 2  # keep retry loop short
        try:
            with pytest.raises(AllProvidersFailedError) as exc_info:
                await router.chat([{"role": "user", "content": "hi"}])
            msg = str(exc_info.value)
            assert "a" in msg and "ma" in msg
            assert "b" in msg and "mb" in msg
            assert "pass" in msg
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_error_carries_tried_attribute(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _resp(404)

        router = self._two_provider_router(handler)
        try:
            with pytest.raises(AllProvidersFailedError) as exc_info:
                await router.chat([{"role": "user", "content": "hi"}])
            tried = exc_info.value.tried
            assert isinstance(tried, list) and len(tried) == 2
            assert {t["provider"] for t in tried} == {"a", "b"}
            for t in tried:
                assert set(t) >= {"provider", "model", "pass"}
                assert "error" in t or "outcome" in t
        finally:
            await router.shutdown()


class TestChainTrace(ChainTestBase):
    @pytest.mark.asyncio
    async def test_trace_recorded_on_success(self):
        calls = {"a": 0, "b": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "mock-a":
                calls["a"] += 1
                return _resp(429)
            calls["b"] += 1
            return _resp(200, "ok-b")

        router = self._two_provider_router(handler)
        try:
            await router.chat([{"role": "user", "content": "hi"}])
            trace = router.get_chain_trace()
            assert len(trace) == 2
            assert trace[0]["provider"] == "a" and "error" in trace[0]
            assert trace[1]["provider"] == "b" and trace[1]["outcome"] == "success"
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_get_chain_trace_returns_copy(self):
        router = self._make_router(lambda r: CHAT_OK, [("p", "http://m", {"chat": "m"}, 0)])
        try:
            await router.chat([{"role": "user", "content": "hi"}])
            trace = router.get_chain_trace()
            trace.clear()
            assert len(router.get_chain_trace()) == 1  # internal not mutated
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_trace_reflects_latest_call(self):
        state = {"mode": "fail"}

        def handler(request: httpx.Request) -> httpx.Response:
            return _resp(404) if state["mode"] == "fail" else CHAT_OK

        router = self._make_router(handler, [("p", "http://mock", {"chat": "m"}, 0)])
        try:
            with pytest.raises(AllProvidersFailedError):
                await router.chat([{"role": "user", "content": "hi"}])
            assert router.get_chain_trace()[0]["pass"] == 1
            state["mode"] = "ok"
            await router.chat([{"role": "user", "content": "hi"}])
            trace = router.get_chain_trace()
            assert len(trace) == 1 and trace[0]["outcome"] == "success"
        finally:
            await router.shutdown()


class TestEmbedChain(ChainTestBase):
    @pytest.mark.asyncio
    async def test_embed_fallback_to_second_provider(self):
        calls = {"a": 0, "b": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "mock-a":
                calls["a"] += 1
                return _resp(429)
            calls["b"] += 1
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5]}]})

        router = self._two_provider_router(handler)
        try:
            vecs = await router.embed(["hello world"])
            assert vecs == [[0.5]]
            assert calls["a"] == 1  # fast-switch applies to embeddings too
            assert calls["b"] == 1
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_embed_exhaustion_lists_models(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _resp(404)

        router = self._two_provider_router(handler)
        try:
            with pytest.raises(AllProvidersFailedError) as exc_info:
                await router.embed(["x"])
            msg = str(exc_info.value)
            assert "ea" in msg and "eb" in msg
        finally:
            await router.shutdown()


class TestKeylessProvider(ChainTestBase):
    @pytest.mark.asyncio
    async def test_keyless_request_omits_auth_header(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return CHAT_OK

        router = self._make_router(
            handler, [("ollama", "http://local", {"chat": "llama3.2"}, 0, False)]
        )
        try:
            result = await router.chat([{"role": "user", "content": "hi"}])
            assert result["choices"][0]["message"]["content"] == "ok"
            assert seen["auth"] is None  # no Authorization header sent
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_keyless_provider_usable_as_fallback(self):
        """OpenAI rate-limited → local Ollama (keyless) picks up the turn."""
        calls = {"cloud": 0, "local": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "mock-cloud":
                calls["cloud"] += 1
                return _resp(429)
            calls["local"] += 1
            return _resp(200, "local-answer")

        router = self._make_router(
            handler,
            [
                ("cloud", "http://mock-cloud", {"chat": "gpt"}, 0),
                ("local", "http://mock-local", {"chat": "llama3.2"}, 10, False),
            ],
        )
        try:
            result = await router.chat([{"role": "user", "content": "hi"}])
            assert result["choices"][0]["message"]["content"] == "local-answer"
            assert calls["cloud"] == 1
            assert calls["local"] == 1
        finally:
            await router.shutdown()


# ── 显式model候选降级（备胎降级语义，2026-09-25 live实锤修复） ──────────
# Live根因：`ollama/mimo-v2.5-pro` ——显式model劫持备胎link，备胎永远拿不到
# 自己声明的模型，fallback链形同虚设（journalctl实录4链attempt全灭）。语义：
#   1. 主link：显式model恒赢优先（调用方点名），声明的role/task模型作后备
#   2. 备胎link声明了显式model → 显式优先（同模型多网关failover）
#   3. 备胎link没声明显式model → 自己声明的模型优先，显式model作后备
#      （模型目录失真/占位配置的自愈路径——token-plan `partial-test` 400后
#      接住显式 `mimo-v2.5-pro` 的实证场景）
#   4. 模型级拒绝（400/404/422）滑到下一候选；端点级错误（401/402/429/5xx）
#      整link放弃——另一个模型救不了一个死端点
#   5. tp- key 走 api-key 头（与 api/llm.py list_models/test_connection 约定一致）


class TestModelCandidates:
    def test_no_explicit_single_candidate(self):
        assert _model_candidates({"chat": "m"}, "m", None) == ("m",)

    def test_declared_explicit_first_then_own(self):
        # provider声明了显式model → 显式优先，role模型后备
        assert _model_candidates({"chat": "x", "summarize": "s"}, "s", "x") == ("x", "s")

    def test_undeclared_own_first_then_explicit(self):
        # 备胎没声明显式model → 自己声明的模型优先（反劫持核心断言）
        assert _model_candidates({"chat": "mb"}, "mb", "ma") == ("mb", "ma")

    def test_primary_undeclared_explicit_first(self):
        # 主link显式model恒赢优先（调用方点名契约），自己声明的模型后备
        assert _model_candidates({"chat": "own-m"}, "own-m", "ask-m", is_primary=True) == (
            "ask-m",
            "own-m",
        )

    def test_dedupe_same_model(self):
        assert _model_candidates({"chat": "ma"}, "ma", "ma") == ("ma",)

    def test_missing_own_keeps_explicit(self):
        assert _model_candidates({}, None, "ma") == ("ma",)

    def test_empty_when_nothing_resolves(self):
        assert _model_candidates({}, None, None) == ()


class TestModelRejectedClassification:
    def test_model_level_statuses(self):
        for status in (400, 404, 422):
            req = httpx.Request("POST", "http://x/c")
            resp = httpx.Response(status, request=req)
            exc = httpx.HTTPStatusError("e", request=req, response=resp)
            assert _is_model_rejected(exc), status

    def test_endpoint_level_statuses_not_model_rejected(self):
        for status in (401, 402, 429, 500, 503):
            req = httpx.Request("POST", "http://x/c")
            resp = httpx.Response(status, request=req)
            exc = httpx.HTTPStatusError("e", request=req, response=resp)
            assert not _is_model_rejected(exc), status

    def test_transport_error_not_model_rejected(self):
        assert not _is_model_rejected(httpx.ConnectError("refused"))


class TestAuthHeaders:
    def test_tp_key_uses_api_key_header(self):
        assert _auth_headers("tp-abc") == {"api-key": "tp-abc"}

    def test_bearer_key_uses_authorization(self):
        assert _auth_headers("sk-abc") == {"Authorization": "Bearer sk-abc"}

    def test_keyless_no_headers(self):
        assert _auth_headers("") == {}
        assert _auth_headers(None) == {}


class TestModelCandidateDegradation(ChainTestBase):
    @pytest.mark.asyncio
    async def test_explicit_model_does_not_hijack_backup_link(self):
        """备胎link先试自己声明的模型——显式model不再劫持备胎（回归核心）。"""
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            model = json.loads(request.content)["model"]
            seen.append((request.url.host, model))
            if request.url.host == "mock-a":
                return _resp(402)  # 端点级：quota耗尽（live实录形态）
            assert model == "mb"  # 备胎拿到的是自己声明的模型，不是显式model
            return _resp(200, "from-b")

        router = self._two_provider_router(
            handler, a_models={"chat": "ma"}, b_models={"chat": "mb"}
        )
        try:
            result = await router.chat([{"role": "user", "content": "hi"}], model="ma")
            assert result["choices"][0]["message"]["content"] == "from-b"
            assert seen == [("mock-a", "ma"), ("mock-b", "mb")]
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_backup_self_heals_placeholder_model_config(self):
        """备胎模型目录失真/占位配置自愈：声明模型被拒(400)→显式model接住。

        live实证场景：LLM_SUBSCRIPTION_MODEL=partial-test 是占位符，token-plan
        返回 400 "Unsupported model"，但同端点服务显式 mimo-v2.5-pro。
        """
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            model = json.loads(request.content)["model"]
            seen.append((request.url.host, model))
            if request.url.host == "mock-a":
                return _resp(402)
            if model == "partial-test":
                return _resp(400)
            assert model == "ma"
            return _resp(200, "healed")

        router = self._two_provider_router(
            handler, a_models={"chat": "ma"}, b_models={"chat": "partial-test"}
        )
        try:
            result = await router.chat([{"role": "user", "content": "hi"}], model="ma")
            assert result["choices"][0]["message"]["content"] == "healed"
            assert seen == [
                ("mock-a", "ma"),
                ("mock-b", "partial-test"),
                ("mock-b", "ma"),
            ]
            trace = router.get_chain_trace()
            rejected = [t for t in trace if t.get("outcome") == "model_rejected"]
            assert len(rejected) == 1 and rejected[0]["model"] == "partial-test"
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_declared_explicit_model_failover_same_model(self):
        """备胎也声明了显式model → 同模型failover，单次调用（不浪费候选）。"""
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            model = json.loads(request.content)["model"]
            seen.append((request.url.host, model))
            if request.url.host == "mock-a":
                return _resp(402)
            return _resp(200, "same-model-b")

        router = self._two_provider_router(
            handler, a_models={"chat": "ma"}, b_models={"chat": "ma", "embedding": "eb"}
        )
        try:
            result = await router.chat([{"role": "user", "content": "hi"}], model="ma")
            assert result["choices"][0]["message"]["content"] == "same-model-b"
            assert seen == [("mock-a", "ma"), ("mock-b", "ma")]
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_primary_explicit_first_then_own_model(self):
        """主link：显式model优先（调用方点名），被拒→自己声明的模型接住。"""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            model = json.loads(request.content)["model"]
            seen.append(model)
            if model == "ask-m":
                return _resp(404)
            return _resp(200, "own-answer")

        router = self._make_router(handler, [("solo", "http://mock", {"chat": "own-m"}, 0)])
        try:
            result = await router.chat([{"role": "user", "content": "hi"}], model="ask-m")
            assert result["choices"][0]["message"]["content"] == "own-answer"
            assert seen == ["ask-m", "own-m"]
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_endpoint_level_error_skips_remaining_candidates(self):
        """端点级错误（402）不烧第二候选——另一个模型救不了死端点。"""
        calls = {"a": 0, "b": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "mock-a":
                calls["a"] += 1
                return _resp(402)
            calls["b"] += 1
            return _resp(402)

        router = self._two_provider_router(
            handler, a_models={"chat": "ma"}, b_models={"chat": "mb"}
        )
        try:
            with pytest.raises(AllProvidersFailedError):
                await router.chat([{"role": "user", "content": "hi"}], model="ma")
            assert calls["a"] == 1
            assert calls["b"] == 1  # 备胎第一候选402后立即放弃整link
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_failure_marked_once_per_link(self):
        """多候选link全灭只记一次failure mark（防备胎冷却被候选数放大）。"""
        calls = {"b": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "mock-a":
                return _resp(402)
            calls["b"] += 1
            return _resp(404)

        router = self._two_provider_router(
            handler, a_models={"chat": "ma"}, b_models={"chat": "mb"}
        )
        try:
            with pytest.raises(AllProvidersFailedError):
                await router.chat([{"role": "user", "content": "hi"}], model="ma")
            assert calls["b"] == 2  # 两候选各试一次
            assert router.providers["b"]._consecutive_failures == 1  # 一次mark
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_embed_backup_uses_own_model(self):
        """embed链对称：显式embedding model不劫持备胎link。"""
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            model = json.loads(request.content)["model"]
            seen.append((request.url.host, model))
            if request.url.host == "mock-a":
                return _resp(429)
            assert model == "eb"
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.7]}]})

        router = self._two_provider_router(handler)
        try:
            vecs = await router.embed(["hello"], model="ea")
            assert vecs == [[0.7]]
            assert seen == [("mock-a", "ea"), ("mock-b", "eb")]
        finally:
            await router.shutdown()
