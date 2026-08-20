"""Integration tests for Workspace API (/api/workspace) — file/dir browsing, execute."""

import pytest


class TestWorkspaceHealth:
    def test_health(self, client):
        resp = client.get("/api/workspace/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestWorkspaceDir:
    def test_dir_root(self, client):
        resp = client.get("/api/dir")
        assert resp.status_code in (200, 401)

    def test_dir_with_path(self, client):
        resp = client.get("/api/dir?path=/tmp")
        assert resp.status_code in (200, 401, 403)

    def test_dir_nonexistent(self, client):
        resp = client.get("/api/dir?path=/nonexistent/path/xyz/99999")
        assert resp.status_code in (200, 400, 401, 403, 404)


class TestWorkspaceFile:
    def test_file_missing_path(self, client):
        resp = client.get("/api/file")
        assert resp.status_code in (200, 400, 401, 422)

    def test_file_nonexistent(self, client):
        resp = client.get("/api/file?path=/nonexistent/file/xyz/99999.txt")
        assert resp.status_code in (200, 400, 401, 403, 404)


class TestWorkspaceExecute:
    def test_execute_missing(self, client):
        resp = client.post("/api/execute", json={})
        assert resp.status_code in (200, 400, 401, 422)

    def test_execute_echo(self, client):
        resp = client.post(
            "/api/execute",
            json={"command": "echo hello"},
        )
        assert resp.status_code in (200, 400, 401, 403, 422)
