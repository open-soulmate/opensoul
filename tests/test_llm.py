"""Integration tests for LLM Proxy API — /api/llm/* endpoints."""

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
        """POST /api/llm/config 会持久化写穿live .env（save_config→_save_overrides_to_env）。

        2026-09-19实证：本套件的config测试曾把生产LLM_BASE_URL覆盖为
        http://test.example.com、LLM_MODEL覆盖为partial-test且无人恢复——
        生产LLM调用全部指向无效地址。fixture在类内每个测试前快照live配置、
        测试后按 subscription→standard→active_variant+legacy 顺序完整恢复。
        """
        snap = client.get("/api/llm/config").json()
        yield
        std = snap.get("standard") or {}
        sub = snap.get("subscription") or {}
        client.post("/api/llm/config", json={
            "variant": "subscription",
            "base_url": sub.get("base_url", ""),
            "api_key": sub.get("api_key", ""),
            "model": sub.get("model", ""),
        })
        client.post("/api/llm/config", json={
            "variant": "standard",
            "base_url": std.get("base_url", ""),
            "api_key": std.get("api_key", ""),
            "model": std.get("model", ""),
        })
        client.post("/api/llm/config", json={
            "variant": snap.get("active_variant", "standard"),
            "base_url": snap.get("base_url", ""),
            "api_key": snap.get("api_key", ""),
            "model": snap.get("model", ""),
        })

    def test_get_config(self, client):
        resp = client.get("/api/llm/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "base_url" in data
        assert "api_key" in data
        assert "model" in data
        # 契约（2026-09-19实证）：GET /config 默认返回真实key——settings页
        # settings-client.tsx 用该值填充表单并在保存时原样POST回写，
        # 若默认masked，"***"会在下次保存时覆盖真实key（灾难）。
        # 掩码机制通过显式 ?masked=true 验证。
        resp_masked = client.get("/api/llm/config", params={"masked": True})
        assert resp_masked.status_code == 200
        masked = resp_masked.json()
        if masked["api_key"]:
            assert masked["api_key"] == "***"
        # 双体系槽位的key同样受masked参数控制
        for slot in ("standard", "subscription"):
            if masked.get(slot, {}).get("api_key"):
                assert masked[slot]["api_key"] == "***"

    def test_save_config_updates_base_url(self, client):
        resp = client.post(
            "/api/llm/config",
            json={"base_url": "https://test.example.com/v1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["base_url"] == "https://test.example.com/v1"

    def test_save_config_updates_model(self, client):
        resp = client.post(
            "/api/llm/config",
            json={"model": "test-model-v1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "test-model-v1"

    def test_save_config_partial_update(self, client):
        """Only provided fields should be updated."""
        # Get current config
        # Update only model
        resp = client.post("/api/llm/config", json={"model": "partial-test"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "partial-test"

    def test_save_config_empty_body(self, client):
        """Empty body should not change anything."""
        before = client.get("/api/llm/config").json()
        resp = client.post("/api/llm/config", json={})
        assert resp.status_code == 200
        after = resp.json()
        assert before["model"] == after["model"]


class TestLLMTestConnection:
    def test_test_connection_no_key(self, client):
        """Should return 400 if no API key configured."""
        resp = client.post("/api/llm/test")
        # May succeed if key is configured, or fail with 400/502
        assert resp.status_code in (200, 400, 502)


class TestLLMCompletions:
    def test_completions_no_key(self, client):
        """Should return 400 if no API key configured."""
        resp = client.post(
            "/api/llm/completions",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 16,
            },
        )
        # May succeed if key is configured, or fail with 400/502
        assert resp.status_code in (200, 400, 502)

    def test_completions_with_custom_model(self, client):
        """Should accept custom model override."""
        resp = client.post(
            "/api/llm/completions",
            json={
                "messages": [{"role": "user", "content": "Say hi"}],
                "model": "gpt-4o-mini",
                "max_tokens": 8,
            },
        )
        assert resp.status_code in (200, 400, 502)

    def test_completions_with_temperature(self, client):
        """Should accept temperature parameter."""
        resp = client.post(
            "/api/llm/completions",
            json={
                "messages": [{"role": "user", "content": "Test"}],
                "temperature": 0.1,
                "max_tokens": 8,
            },
        )
        assert resp.status_code in (200, 400, 502)
