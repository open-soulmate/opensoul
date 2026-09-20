"""Integration tests for LLM Proxy API — configuration and completions endpoints."""

import pytest


class TestLLMHealth:
    def test_health(self, client):
        resp = client.get("/api/llm/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "LLMProxy"


class TestLLMConfig:
    @pytest.fixture(autouse=True)
    def _restore_live_config(self, client):
        """POST /api/llm/config 持久化写穿live .env——测试后必须完整恢复
        （2026-09-19实证：config测试曾污染生产base_url/model无人恢复）。"""
        snap = client.get("/api/llm/config").json()
        yield
        std = snap.get("standard") or {}
        sub = snap.get("subscription") or {}
        client.post(
            "/api/llm/config",
            json={
                "variant": "subscription",
                "base_url": sub.get("base_url", ""),
                "api_key": sub.get("api_key", ""),
                "model": sub.get("model", ""),
            },
        )
        client.post(
            "/api/llm/config",
            json={
                "variant": "standard",
                "base_url": std.get("base_url", ""),
                "api_key": std.get("api_key", ""),
                "model": std.get("model", ""),
            },
        )
        client.post(
            "/api/llm/config",
            json={
                "variant": snap.get("active_variant", "standard"),
                "base_url": snap.get("base_url", ""),
                "api_key": snap.get("api_key", ""),
                "model": snap.get("model", ""),
            },
        )

    def test_get_config(self, client):
        resp = client.get("/api/llm/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "base_url" in data
        assert "api_key" in data
        assert "model" in data
        # 契约（2026-09-19实证）：默认返回真实key供settings页表单回写
        # （settings-client.tsx:384/:545 填充+原样POST回写，masked默认值会覆盖真实key）；
        # 掩码机制用显式 ?masked=true 验证
        resp_masked = client.get("/api/llm/config", params={"masked": True})
        assert resp_masked.status_code == 200
        masked = resp_masked.json()
        if masked["api_key"]:
            assert masked["api_key"] == "***"

    def test_update_config(self, client):
        resp = client.post(
            "/api/llm/config",
            json={"model": "test-model"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "test-model"

    def test_update_config_partial(self, client):
        """Updating one field should not clear others."""
        # First set a model
        client.post("/api/llm/config", json={"model": "partial-test"})
        # Then update only base_url
        resp = client.post(
            "/api/llm/config",
            json={"base_url": "http://test.example.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["base_url"] == "http://test.example.com"
        # model should still be set (from override or settings)
        assert "model" in data


class TestLLMCompletions:
    def test_completions_no_api_key(self, client):
        """Should return 400 if no API key configured."""
        # Clear any overrides first
        from src.api.llm import _llm_overrides

        saved_key = _llm_overrides.pop("api_key", None)
        saved_base = _llm_overrides.pop("base_url", None)
        saved_model = _llm_overrides.pop("model", None)
        try:
            from src.config import settings

            if not settings.llm_api_key:
                resp = client.post(
                    "/api/llm/completions",
                    json={"messages": [{"role": "user", "content": "hello"}]},
                )
                assert resp.status_code == 400
                assert "API key" in resp.json()["detail"]
        finally:
            if saved_key:
                _llm_overrides["api_key"] = saved_key
            if saved_base:
                _llm_overrides["base_url"] = saved_base
            if saved_model:
                _llm_overrides["model"] = saved_model

    def test_test_endpoint_no_api_key(self, client):
        """Test connection endpoint should return 400 if no key."""
        from src.api.llm import _llm_overrides
        from src.config import settings

        saved_key = _llm_overrides.pop("api_key", None)
        try:
            if not settings.llm_api_key:
                resp = client.post("/api/llm/test")
                assert resp.status_code == 400
        finally:
            if saved_key:
                _llm_overrides["api_key"] = saved_key
