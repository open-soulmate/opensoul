"""Unit + router-level tests for the LLM retry policy.

Ported from kilocode retry.ts (Retry-After three formats, 5xx unconditional
retry, non-retryable exclusions, hard attempt limit). Runs without a live
server — HTTP is mocked with httpx.MockTransport.
"""

import asyncio
import email.utils
import time

import httpx
import pytest

from src.cortex.llm_retry import (
    backoff_delay,
    is_retryable_status,
    parse_retry_after,
    retry_delay_for,
)
from src.gland.router import ModelRouter, TaskType


class TestRetryableStatus:
    def test_transient_5xx_all_retryable(self):
        for status in (500, 502, 503, 504, 522, 524, 599):
            assert is_retryable_status(status), status

    def test_rate_limit_family(self):
        for status in (408, 425, 429):
            assert is_retryable_status(status), status

    def test_client_errors_not_retryable(self):
        for status in (400, 401, 403, 404, 409, 422):
            assert not is_retryable_status(status), status


class TestParseRetryAfter:
    def test_ms_header_format(self):
        assert parse_retry_after({"retry-after-ms": "1500"}) == 1.5

    def test_delta_seconds_format(self):
        assert parse_retry_after({"retry-after": "7"}) == 7.0

    def test_delta_seconds_float(self):
        assert parse_retry_after({"retry-after": "2.5"}) == 2.5

    def test_http_date_format(self):
        future = time.time() + 30
        date_str = email.utils.formatdate(future, usegmt=True)
        val = parse_retry_after({"retry-after": date_str})
        assert val is not None
        assert 25 <= val <= 30

    def test_http_date_in_past_clamps_to_zero(self):
        past = email.utils.formatdate(time.time() - 100, usegmt=True)
        assert parse_retry_after({"retry-after": past}) == 0.0

    def test_capped_at_limit(self):
        assert parse_retry_after({"retry-after": "999999"}) == 120.0

    def test_garbage_value_returns_none(self):
        assert parse_retry_after({"retry-after": "soon"}) is None

    def test_missing_headers_returns_none(self):
        assert parse_retry_after({}) is None
        assert parse_retry_after(None) is None

    def test_ms_takes_precedence_over_seconds(self):
        headers = {"retry-after-ms": "500", "retry-after": "60"}
        assert parse_retry_after(headers) == 0.5


class TestBackoffDelay:
    def test_header_wins_over_exponential(self):
        # Attempt 0 would be 2s exponential; header says 0.25s.
        assert backoff_delay(0, {"retry-after-ms": "250"}, jitter=False) == 0.25

    def test_exponential_no_header(self):
        assert backoff_delay(0, None, jitter=False) == 2.0
        assert backoff_delay(1, None, jitter=False) == 4.0
        assert backoff_delay(2, None, jitter=False) == 8.0

    def test_exponential_capped_at_30s(self):
        assert backoff_delay(10, None, jitter=False) == 30.0

    def test_jitter_stays_in_half_range(self):
        for _ in range(50):
            d = backoff_delay(3, None, jitter=True)  # base 16s
            assert 8.0 <= d <= 16.0


class TestRetryDelayFor:
    def _status_exc(self, status: int, headers: dict | None = None):
        req = httpx.Request("POST", "http://mock/chat/completions")
        resp = httpx.Response(status, headers=headers or {}, request=req)
        return httpx.HTTPStatusError("err", request=req, response=resp)

    def test_429_with_header_uses_header(self):
        exc = self._status_exc(429, {"retry-after-ms": "300"})
        assert retry_delay_for(exc, 0) == 0.3

    def test_5xx_without_header_uses_backoff(self):
        exc = self._status_exc(503)
        d = retry_delay_for(exc, 0)
        assert 1.0 <= d <= 2.0  # 2s base, half jitter

    def test_401_returns_none(self):
        assert retry_delay_for(self._status_exc(401), 0) is None

    def test_transport_error_retryable(self):
        exc = httpx.ConnectError("connection refused")
        d = retry_delay_for(exc, 0)
        assert d is not None
        assert 0 < d <= 30

    def test_unknown_exception_not_retryable(self):
        assert retry_delay_for(ValueError("boom"), 0) is None


class TestRouterRetryIntegration:
    """Router-level behavior with a mocked provider endpoint."""

    def _make_router(self, handler) -> ModelRouter:
        router = ModelRouter()
        router._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)
        router.key_manager.add_key("mock", "test-key")
        router.add_provider("mock", "http://mock", models={"chat": "m", "embedding": "e"})
        return router

    @pytest.mark.asyncio
    async def test_429_then_success_retries_same_provider(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    429,
                    headers={"retry-after-ms": "10"},
                    json={"error": "rate limited"},
                )
            return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

        router = self._make_router(handler)
        try:
            result = await router.chat([{"role": "user", "content": "hi"}])
            assert result["choices"][0]["message"]["content"] == "pong"
            assert calls["n"] == 2  # one retry, same provider
            # Success after retry must not leave failure marks / cooldown.
            assert router.providers["mock"]._consecutive_failures == 0
            assert router.providers["mock"]._cooldown_until == 0.0
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_500_retries_up_to_attempt_limit(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500, json={"error": "server"})

        router = self._make_router(handler)
        router.RETRY_MAX_ATTEMPTS = 3
        try:
            from src.gland.router import AllProvidersFailedError

            with pytest.raises(AllProvidersFailedError):
                await router.chat([{"role": "user", "content": "hi"}])
            assert calls["n"] == 3  # bounded, no infinite loop
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_401_no_retry_fails_over_immediately(self):
        calls = {"a": 0, "b": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "mock-a":
                calls["a"] += 1
                return httpx.Response(401, json={"error": "bad key"})
            calls["b"] += 1
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok-b"}}]})

        router = ModelRouter()
        router._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)
        router.key_manager.add_key("a", "key-a")
        router.key_manager.add_key("b", "key-b")
        router.add_provider("a", "http://mock-a", models={"chat": "m"}, priority=0)
        router.add_provider("b", "http://mock-b", models={"chat": "m"}, priority=1)
        try:
            result = await router.chat([{"role": "user", "content": "hi"}])
            assert result["choices"][0]["message"]["content"] == "ok-b"
            assert calls["a"] == 1  # auth error: zero retries
            assert calls["b"] == 1
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_embedding_retry(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(502, json={"error": "bad gateway"})
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

        router = self._make_router(handler)
        try:
            vecs = await router.embed(["hello"])
            assert vecs == [[0.1, 0.2]]
            assert calls["n"] == 2
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_connection_error_retries_then_succeeds(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("econnrefused")
            return httpx.Response(200, json={"choices": [{"message": {"content": "recovered"}}]})

        router = self._make_router(handler)
        try:
            result = await router.chat([{"role": "user", "content": "hi"}])
            assert result["choices"][0]["message"]["content"] == "recovered"
            assert calls["n"] == 2
        finally:
            await router.shutdown()
