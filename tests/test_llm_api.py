"""Integration tests for LLM Proxy API."""


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

    def test_save_config(self, client):
        # Get current config first
        current = client.get("/api/llm/config").json()
        # Save with same values (no-op update)
        resp = client.post("/api/llm/config", json={})
        assert resp.status_code == 200
