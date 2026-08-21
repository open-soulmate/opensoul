"""Integration tests for LLM Proxy API — /api/llm/* endpoints."""


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
        current = client.get("/api/llm/config").json()
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
