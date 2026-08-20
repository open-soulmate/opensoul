"""Integration tests for Knowledge Base Sharing API."""


class TestKbSharingHealth:
    def test_health(self, client):
        resp = client.get("/api/kb-sharing/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestKbSharingList:
    def test_list_requests(self, client):
        resp = client.get("/api/kb-sharing/")
        # May require auth or return non-list format
        assert resp.status_code in (200, 401, 403)
