"""Tests for OpenKnowledge — knowledge base management."""


class TestKnowledgeHealth:
    def test_health(self, client):
        resp = client.get("/api/knowledge/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenKnowledge"


class TestKnowledgeStats:
    def test_stats(self, client):
        resp = client.get("/api/knowledge/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenKnowledge"
        assert "total_entries" in data
        assert "recent_24h" in data
        assert "top_users" in data


class TestKnowledgeCRUD:
    def test_list_knowledge_requires_user_id(self, client):
        resp = client.get("/api/knowledge/")
        # Missing user_id query param — should fail validation
        assert resp.status_code in (400, 401, 403, 422)

    def test_list_knowledge_with_user_id(self, client):
        import uuid

        resp = client.get(
            "/api/knowledge/",
            params={"user_id": str(uuid.uuid4())},
        )
        assert resp.status_code in (200, 401, 403)

    def test_create_knowledge_requires_body(self, client):
        resp = client.post("/api/knowledge/", json={})
        assert resp.status_code in (400, 401, 403, 422)

    def test_get_nonexistent_knowledge(self, client):
        resp = client.get("/api/knowledge/00000000-0000-0000-0000-000000000000")
        assert resp.status_code in (404, 401, 403, 422)

    def test_delete_nonexistent_knowledge(self, client):
        resp = client.delete("/api/knowledge/00000000-0000-0000-0000-000000000000")
        assert resp.status_code in (404, 401, 403, 422)

    def test_upload_requires_file(self, client):
        resp = client.post("/api/knowledge/upload")
        assert resp.status_code in (400, 401, 403, 422)
