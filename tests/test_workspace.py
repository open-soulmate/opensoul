"""Integration tests for OpenWorkspace — file operations and command execution."""


class TestWorkspaceHealth:
    def test_health(self, client):
        resp = client.get("/api/workspace/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "OpenWorkspace"


class TestDirectoryListing:
    def test_list_home(self, client):
        resp = client.get("/api/dir?path=~")
        assert resp.status_code == 200
        data = resp.json()
        assert "path" in data
        assert "entries" in data
        assert isinstance(data["entries"], list)

    def test_list_tmp(self, client):
        resp = client.get("/api/dir?path=/tmp")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data

    def test_list_nonexistent(self, client):
        resp = client.get("/api/dir?path=/nonexistent/path/that/doesnt/exist")
        assert resp.status_code in (400, 403, 404)

    def test_list_blocked_path(self, client):
        resp = client.get("/api/dir?path=/etc/shadow")
        # Should be blocked by safety check (path validation)
        assert resp.status_code in (400, 403)


class TestCommandExecution:
    def test_execute_simple(self, client):
        resp = client.post(
            "/api/execute",
            json={"cmd": "echo hello", "timeout": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "hello" in data.get("output", "")

    def test_execute_with_cwd(self, client):
        resp = client.post(
            "/api/execute",
            json={"cmd": "pwd", "cwd": "/tmp", "timeout": 5},
        )
        assert resp.status_code == 200

    def test_execute_timeout(self, client):
        resp = client.post(
            "/api/execute",
            json={"cmd": "sleep 60", "timeout": 1},
        )
        # Should either return 200 with error or 408/500
        assert resp.status_code in (200, 408, 500)
