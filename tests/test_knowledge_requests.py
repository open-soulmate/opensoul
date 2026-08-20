"""Integration tests for Knowledge Requests API."""


class TestKnowledgeRequestsHealth:
    def test_health(self, client):
        resp = client.get("/api/knowledge-requests/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestKnowledgeRequestsList:
    def test_list_requests(self, client):
        resp = client.get("/api/knowledge-requests/")
        # May require auth, so 200 or 401/403 are acceptable
        assert resp.status_code in (200, 401, 403)
