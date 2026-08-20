"""Integration tests for OpenSoul Marketplace API."""


class TestMarketplaceHealth:
    def test_health_returns_200(self, client):
        resp = client.get("/api/marketplace/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestMarketplaceSkillSources:
    def test_list_skill_sources(self, client):
        resp = client.get("/api/marketplace/skills/sources")
        # May require auth
        assert resp.status_code in (200, 401, 403)


class TestMarketplaceAgentSources:
    def test_list_agent_sources(self, client):
        resp = client.get("/api/marketplace/agents/sources")
        assert resp.status_code in (200, 401, 403)
