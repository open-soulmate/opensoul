"""`_probe_variant` 探针状态全量分类测试（dev-report 2026-09-25 遗留#3销账）。

弱断言缺陷实锤形态（live 2026-09-25）：chat 探针只判 402，400「Unsupported model」
和 401 都被误判成 ok——partial-test 占位符配置的绿灯是假绿，差点误导 fallback 链
备胎判断。本文件用 httpx.MockTransport 锁死五态+unknown 分类语义。

纯单元测试，不依赖 live 服务（_probe_client 测试 seam 换 MockTransport）。
"""

import asyncio
import json

import httpx
import pytest

from src.api.llm import _auth_headers, _probe_variant


def _mock_probe(monkeypatch, handler):
    """把 _probe_client 换成 MockTransport 客户端工厂。"""

    def factory(timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr("src.api.llm._probe_client", factory)


def probe(base_url: str, api_key: str, model: str = "") -> str:
    """同步驱动异步探针（asyncio.run，测试无需事件循环插件约束）。"""
    return asyncio.run(_probe_variant(base_url, api_key, model))


def _models_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]})


class TestAuthHeaders:
    def test_tp_key_uses_api_key_header(self):
        assert _auth_headers("tp-abc") == {"api-key": "tp-abc"}

    def test_normal_key_uses_bearer(self):
        assert _auth_headers("sk-abc") == {"Authorization": "Bearer sk-abc"}

    def test_keyless_no_header(self):
        assert _auth_headers("") == {}
        assert _auth_headers(None) == {}


class TestProbeClassification:
    def test_not_configured_without_key(self):
        assert probe("http://x", "", "m") == "not_configured"

    def test_not_configured_without_url(self):
        assert probe("", "sk-1", "m") == "not_configured"

    def test_unreachable_on_models_transport_error(self, monkeypatch):
        def handler(request):
            raise httpx.ConnectError("boom")

        _mock_probe(monkeypatch, handler)
        assert probe("http://x", "sk-1", "m") == "unreachable"

    def test_invalid_key_on_models_401(self, monkeypatch):
        _mock_probe(monkeypatch, lambda r: httpx.Response(401))
        assert probe("http://x", "sk-1", "m") == "invalid_key"

    def test_low_balance_on_models_402(self, monkeypatch):
        _mock_probe(monkeypatch, lambda r: httpx.Response(402))
        assert probe("http://x", "sk-1", "m") == "low_balance"

    def test_error_status_on_models_other(self, monkeypatch):
        _mock_probe(monkeypatch, lambda r: httpx.Response(500))
        assert probe("http://x", "sk-1", "m") == "error_500"

    def test_ok_requires_real_chat_200(self, monkeypatch):
        def handler(request):
            if request.url.path.endswith("/models"):
                return _models_ok(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

        _mock_probe(monkeypatch, handler)
        assert probe("http://x", "sk-1", "m2") == "ok"

    def test_unsupported_model_on_chat_400(self, monkeypatch):
        """live实锤形态：token-plan对占位符model返回400 Unsupported model。
        修复前误判ok（假绿灯）——本用例即回归锁。"""

        def handler(request):
            if request.url.path.endswith("/models"):
                return _models_ok(request)
            return httpx.Response(400, json={"error": {"message": "Unsupported model"}})

        _mock_probe(monkeypatch, handler)
        assert probe("http://x", "tp-1", "partial-test") == "unsupported_model"

    @pytest.mark.parametrize("code", [400, 404, 422])
    def test_model_rejected_statuses(self, monkeypatch, code):
        def handler(request):
            if request.url.path.endswith("/models"):
                return _models_ok(request)
            return httpx.Response(code)

        _mock_probe(monkeypatch, handler)
        assert probe("http://x", "sk-1", "m") == "unsupported_model"

    def test_invalid_key_on_chat_401(self, monkeypatch):
        """修复前chat 401落进except→ok（假绿）。"""

        def handler(request):
            if request.url.path.endswith("/models"):
                return _models_ok(request)
            return httpx.Response(401)

        _mock_probe(monkeypatch, handler)
        assert probe("http://x", "sk-1", "m") == "invalid_key"

    def test_low_balance_on_chat_402(self, monkeypatch):
        def handler(request):
            if request.url.path.endswith("/models"):
                return _models_ok(request)
            return httpx.Response(402)

        _mock_probe(monkeypatch, handler)
        assert probe("http://x", "sk-1", "m") == "low_balance"

    def test_error_status_on_chat_other(self, monkeypatch):
        def handler(request):
            if request.url.path.endswith("/models"):
                return _models_ok(request)
            return httpx.Response(429)

        _mock_probe(monkeypatch, handler)
        assert probe("http://x", "sk-1", "m") == "error_429"

    def test_chat_transport_error_is_unknown_not_ok(self, monkeypatch):
        """/models已通但chat断连→unknown（不许冒充ok）。修复前=ok。"""

        def handler(request):
            if request.url.path.endswith("/models"):
                return _models_ok(request)
            raise httpx.ReadTimeout("slow")

        _mock_probe(monkeypatch, handler)
        assert probe("http://x", "sk-1", "m") == "unknown"

    def test_empty_model_probes_with_first_listed(self, monkeypatch):
        seen = {}

        def handler(request):
            if request.url.path.endswith("/models"):
                return _models_ok(request)
            seen["model"] = json.loads(request.content)["model"]
            return httpx.Response(200, json={"choices": []})

        _mock_probe(monkeypatch, handler)
        assert probe("http://x", "sk-1", "") == "ok"
        assert seen["model"] == "m1"

    def test_empty_model_unparseable_list_is_unknown(self, monkeypatch):
        def handler(request):
            return httpx.Response(200, json={"unexpected": "shape"})

        _mock_probe(monkeypatch, handler)
        assert probe("http://x", "sk-1", "") == "unknown"


class TestProbeAuthProtocol:
    def test_tp_key_sent_as_api_key_header(self, monkeypatch):
        """_probe_variant此前是api/llm.py里唯一漏掉tp-认证协议的HTTP路径。"""
        seen = []

        def handler(request):
            seen.append(dict(request.headers))
            return _models_ok(request)

        _mock_probe(monkeypatch, handler)
        assert probe("http://x", "tp-abc", "m1") == "ok"
        assert len(seen) == 2  # /models + /chat 都带对头
        for h in seen:
            assert h.get("api-key") == "tp-abc"
            assert "authorization" not in h

    def test_normal_key_sent_as_bearer(self, monkeypatch):
        seen = []

        def handler(request):
            seen.append(dict(request.headers))
            return _models_ok(request)

        _mock_probe(monkeypatch, handler)
        assert probe("http://x", "sk-abc", "m1") == "ok"
        for h in seen:
            assert h.get("authorization") == "Bearer sk-abc"
            assert "api-key" not in h
