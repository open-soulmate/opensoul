"""Integration tests for MCP (Model Context Protocol) API — server management."""


class TestMCPHealth:
    def test_health(self, client):
        resp = client.get("/api/mcp/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "mcp"


class TestMCPServers:
    def test_list_servers(self, client):
        resp = client.get("/api/mcp/servers")
        assert resp.status_code == 200
        data = resp.json()
        assert "servers" in data
        assert "total" in data
        assert isinstance(data["servers"], list)

    def test_list_servers_enabled_only(self, client):
        resp = client.get("/api/mcp/servers?enabled_only=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "servers" in data

    def test_add_server(self, client):
        resp = client.post(
            "/api/mcp/servers",
            json={
                "name": "test-mcp-server",
                "url": "stdio://test-server",
                "description": "Test MCP server for integration tests",
                "transport": "stdio",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test-mcp-server"
        assert "id" in data
        server_id = data["id"]

        # Get the server by ID
        resp2 = client.get(f"/api/mcp/servers/{server_id}")
        assert resp2.status_code == 200
        assert resp2.json()["name"] == "test-mcp-server"

        # Clean up
        resp3 = client.delete(f"/api/mcp/servers/{server_id}")
        assert resp3.status_code == 200
        assert resp3.json()["deleted"] is True

    def test_get_nonexistent_server(self, client):
        resp = client.get("/api/mcp/servers/nonexistent-id")
        assert resp.status_code == 404

    def test_delete_nonexistent_server(self, client):
        resp = client.delete("/api/mcp/servers/nonexistent-id")
        assert resp.status_code == 404

    def test_update_server(self, client):
        # Create a server
        create_resp = client.post(
            "/api/mcp/servers",
            json={
                "name": "update-test-server",
                "url": "stdio://update-test",
            },
        )
        assert create_resp.status_code == 200
        server_id = create_resp.json()["id"]

        try:
            # Update it
            resp = client.patch(
                f"/api/mcp/servers/{server_id}",
                json={"description": "Updated description", "enabled": False},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["description"] == "Updated description"
            assert data["enabled"] is False
        finally:
            client.delete(f"/api/mcp/servers/{server_id}")

    def test_update_nonexistent_server(self, client):
        resp = client.patch(
            "/api/mcp/servers/nonexistent-id",
            json={"description": "nope"},
        )
        assert resp.status_code == 404


class TestMCPTools:
    def test_list_tools(self, client):
        resp = client.get("/api/mcp/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert "total" in data
        assert isinstance(data["tools"], list)

    def test_list_tools_filtered(self, client):
        resp = client.get("/api/mcp/tools?server_id=nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


class TestMCPStats:
    def test_stats(self, client):
        resp = client.get("/api/mcp/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestMCPConnection:
    def test_connect_nonexistent(self, client):
        resp = client.post("/api/mcp/servers/nonexistent/connect")
        # Should fail (server doesn't exist or can't connect)
        assert resp.status_code in (400, 404, 500)

    def test_disconnect_nonexistent(self, client):
        resp = client.post("/api/mcp/servers/nonexistent/disconnect")
        assert resp.status_code in (400, 404, 500)
