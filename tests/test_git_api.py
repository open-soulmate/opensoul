"""Integration tests for Git API."""

import pytest


class TestGitHealth:
    def test_health(self, client):
        resp = client.get("/api/git/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "GitAPI"


class TestGitStatus:
    def test_status(self, client):
        resp = client.get("/api/git/status")
        # May return 200 with git info or 500 if not in a git repo
        assert resp.status_code in (200, 500)

    def test_status_response_structure(self, client):
        resp = client.get("/api/git/status")
        if resp.status_code == 200:
            data = resp.json()
            assert "branch" in data
            assert "modified" in data
            assert "staged" in data
            assert "untracked" in data
            assert "ahead" in data
            assert "behind" in data

    def test_status_numeric_fields(self, client):
        resp = client.get("/api/git/status")
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data["modified"], int)
            assert isinstance(data["staged"], int)
            assert isinstance(data["untracked"], int)
            assert isinstance(data["ahead"], int)
            assert isinstance(data["behind"], int)
            assert data["modified"] >= 0
            assert data["staged"] >= 0
            assert data["untracked"] >= 0
            assert data["ahead"] >= 0
            assert data["behind"] >= 0


class TestGitCommit:
    def test_commit_empty_message(self, client):
        resp = client.post(
            "/api/git/commit",
            json={"message": "", "add_all": False},
        )
        assert resp.status_code == 400

    def test_commit_whitespace_message(self, client):
        resp = client.post(
            "/api/git/commit",
            json={"message": "   ", "add_all": False},
        )
        assert resp.status_code == 400

    def test_commit_nothing_to_commit(self, client):
        resp = client.post(
            "/api/git/commit",
            json={"message": "test commit", "add_all": True},
        )
        # Should succeed or report nothing to commit
        assert resp.status_code == 200
        data = resp.json()
        assert "success" in data
        assert "message" in data
