"""Integration tests for MCP (Model Context Protocol) API."""


class TestMCPHealth:
    def test_health(self, client):
        resp = client.get("/api/mcp/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestMCPServers:
    def test_list_servers(self, client):
        resp = client.get("/api/mcp/servers")
        assert resp.status_code == 200
        data = resp.json()
        assert "servers" in data
        assert isinstance(data["servers"], list)
        assert "total" in data

    def test_list_servers_enabled_only(self, client):
        resp = client.get("/api/mcp/servers?enabled_only=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "servers" in data
        assert isinstance(data["servers"], list)

    def test_get_nonexistent_server(self, client):
        resp = client.get("/api/mcp/servers/nonexistent-id")
        assert resp.status_code == 404
