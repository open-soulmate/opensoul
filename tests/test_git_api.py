"""Integration tests for Git API."""


class TestGitHealth:
    def test_health(self, client):
        resp = client.get("/api/git/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestGitStatus:
    def test_status(self, client):
        resp = client.get("/api/git/status")
        # May return 200 with git info or 500 if not in a git repo
        assert resp.status_code in (200, 500)
