"""Integration tests for LLM Proxy API — configuration and completions endpoints."""


class TestLLMHealth:
    def test_health(self, client):
        resp = client.get("/api/llm/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "LLMProxy"


class TestLLMConfig:
    def test_get_config(self, client):
        resp = client.get("/api/llm/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "base_url" in data
        assert "api_key" in data
        assert "model" in data
        # API key should be masked
        if data["api_key"]:
            assert data["api_key"] == "***"

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
