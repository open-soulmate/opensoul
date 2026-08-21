"""Integration tests for MCP (Model Context Protocol) API — /api/mcp/* endpoints."""


class TestMCPHealth:
    def test_health(self, client):
        resp = client.get("/api/mcp/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "mcp"


class TestMCPServers:
    def test_list_servers_empty(self, client):
        resp = client.get("/api/mcp/servers")
        assert resp.status_code == 200
        data = resp.json()
        assert "servers" in data
        assert "total" in data
        assert isinstance(data["servers"], list)

    def test_list_servers_enabled_only(self, client):
        resp = client.get("/api/mcp/servers", params={"enabled_only": True})
        assert resp.status_code == 200
        data = resp.json()
        assert "servers" in data

    def test_get_server_not_found(self, client):
        resp = client.get("/api/mcp/servers/nonexistent-id-12345")
        assert resp.status_code == 404

    def test_add_server(self, client):
        resp = client.post(
            "/api/mcp/servers",
            json={
                "name": "test-mcp-server",
                "url": "http://localhost:9999",
                "description": "Test MCP server for integration tests",
                "transport": "sse",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test-mcp-server"
        assert data["url"] == "http://localhost:9999"
        assert "id" in data

        # Cleanup
        client.delete(f"/api/mcp/servers/{data['id']}")

    def test_add_server_with_tools(self, client):
        resp = client.post(
            "/api/mcp/servers",
            json={
                "name": "test-mcp-tools",
                "url": "http://localhost:9998",
                "description": "Server with tools",
                "transport": "stdio",
                "tools": [
                    {"name": "search", "description": "Search tool"},
                    {"name": "fetch", "description": "Fetch tool"},
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test-mcp-tools"
        server_id = data["id"]

        # Verify tools are listed
        tools_resp = client.get("/api/mcp/tools", params={"server_id": server_id})
        assert tools_resp.status_code == 200

        # Cleanup
        client.delete(f"/api/mcp/servers/{server_id}")

    def test_update_server(self, client):
        # Create
        create_resp = client.post(
            "/api/mcp/servers",
            json={
                "name": "update-test",
                "url": "http://localhost:9997",
            },
        )
        assert create_resp.status_code == 200
        server_id = create_resp.json()["id"]

        # Update
        update_resp = client.patch(
            f"/api/mcp/servers/{server_id}",
            json={"name": "updated-name", "enabled": False},
        )
        assert update_resp.status_code == 200
        data = update_resp.json()
        assert data["name"] == "updated-name"

        # Cleanup
        client.delete(f"/api/mcp/servers/{server_id}")

    def test_update_server_not_found(self, client):
        resp = client.patch(
            "/api/mcp/servers/nonexistent-id",
            json={"name": "nope"},
        )
        assert resp.status_code == 404

    def test_delete_server(self, client):
        # Create
        create_resp = client.post(
            "/api/mcp/servers",
            json={"name": "delete-test", "url": "http://localhost:9996"},
        )
        assert create_resp.status_code == 200
        server_id = create_resp.json()["id"]

        # Delete
        del_resp = client.delete(f"/api/mcp/servers/{server_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["deleted"] is True

        # Verify gone
        get_resp = client.get(f"/api/mcp/servers/{server_id}")
        assert get_resp.status_code == 404

    def test_delete_server_not_found(self, client):
        resp = client.delete("/api/mcp/servers/nonexistent-id")
        assert resp.status_code == 404


class TestMCPConnection:
    def test_connect_nonexistent(self, client):
        resp = client.post("/api/mcp/servers/nonexistent-id/connect")
        assert resp.status_code in (400, 404)

    def test_disconnect_nonexistent(self, client):
        resp = client.post("/api/mcp/servers/nonexistent-id/disconnect")
        assert resp.status_code in (400, 404)


class TestMCPTools:
    def test_list_tools_all(self, client):
        resp = client.get("/api/mcp/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert "total" in data

    def test_list_tools_filtered(self, client):
        resp = client.get("/api/mcp/tools", params={"server_id": "none"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


class TestMCPStats:
    def test_get_stats(self, client):
        resp = client.get("/api/mcp/stats")
        assert resp.status_code == 200
        data = resp.json()
        # Should return some statistics structure
        assert isinstance(data, dict)
