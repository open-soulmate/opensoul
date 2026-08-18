"""Integration tests for OpenGene (基因) — template library."""


class TestGeneHealth:
    def test_health(self, client):
        resp = client.get("/api/gene/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenGene"
        assert data["total_templates"] >= 6


class TestGeneTemplates:
    def test_list_templates(self, client):
        resp = client.get("/api/gene/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list) or "templates" in data

    def test_get_template(self, client):
        # List first to get a valid ID
        resp = client.get("/api/gene/templates")
        assert resp.status_code == 200
        templates = (
            resp.json() if isinstance(resp.json(), list) else resp.json().get("templates", [])
        )
        if templates:
            tid = templates[0]["template_id"]
            resp = client.get(f"/api/gene/templates/{tid}")
            assert resp.status_code == 200

    def test_create_and_delete_user_template(self, client):
        resp = client.post(
            "/api/gene/templates",
            json={
                "name": "test_template",
                "category": "agent",
                "description": "Integration test template",
                "config": {"model": "gpt-4", "prompt": "You are a test agent."},
                "variables": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        tid = data["template_id"]

        # Delete
        resp = client.delete(f"/api/gene/templates/{tid}")
        assert resp.status_code == 200
