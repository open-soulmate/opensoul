"""OpenMCP API — Model Context Protocol server management.

Endpoints for registering, connecting, and managing MCP servers and their tools.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.mcp.server_registry import McpServerRegistry

router = APIRouter()

# ── Singleton ──────────────────────────────────────────────
registry = McpServerRegistry()


# ── Request Schemas ────────────────────────────────────────


class ServerCreateRequest(BaseModel):
    name: str
    url: str
    description: str = ""
    transport: str = "stdio"
    config: dict = {}
    tools: list[dict] = []


class ServerUpdateRequest(BaseModel):
    name: str | None = None
    url: str | None = None
    description: str | None = None
    transport: str | None = None
    enabled: bool | None = None
    config: dict | None = None
    tools: list[dict] | None = None


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def health():
    return {"status": "ok", "component": "mcp"}


# ── Server CRUD ────────────────────────────────────────────


@router.get("/servers")
async def list_servers(enabled_only: bool = Query(default=False)):
    """List all registered MCP servers."""
    servers = registry.list_servers(enabled_only=enabled_only)
    return {"servers": servers, "total": len(servers)}


@router.get("/servers/{server_id}")
async def get_server(server_id: str):
    """Get details of a specific MCP server."""
    srv = registry.get_server(server_id)
    if not srv:
        raise HTTPException(404, "MCP server not found")
    return srv.to_dict()


@router.post("/servers")
async def add_server(req: ServerCreateRequest):
    """Register a new MCP server."""
    srv = registry.add_server(
        name=req.name,
        url=req.url,
        description=req.description,
        transport=req.transport,
        config=req.config,
        tools=req.tools,
    )
    return srv.to_dict()


@router.patch("/servers/{server_id}")
async def update_server(server_id: str, req: ServerUpdateRequest):
    """Update an MCP server's configuration."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    srv = registry.update_server(server_id, **updates)
    if not srv:
        raise HTTPException(404, "MCP server not found")
    return srv.to_dict()


@router.delete("/servers/{server_id}")
async def delete_server(server_id: str):
    """Remove an MCP server."""
    if not registry.delete_server(server_id):
        raise HTTPException(404, "MCP server not found")
    return {"deleted": True, "id": server_id}


# ── Connection ─────────────────────────────────────────────


@router.post("/servers/{server_id}/connect")
async def connect_server(server_id: str):
    """Connect to an MCP server and discover its tools."""
    result = registry.connect(server_id)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Connection failed"))
    return result


@router.post("/servers/{server_id}/disconnect")
async def disconnect_server(server_id: str):
    """Disconnect from an MCP server."""
    result = registry.disconnect(server_id)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Disconnect failed"))
    return result


# ── Tools ──────────────────────────────────────────────────


@router.get("/tools")
async def list_tools(server_id: str | None = Query(default=None)):
    """List all tools from enabled MCP servers."""
    tools = registry.list_tools(server_id=server_id)
    return {"tools": tools, "total": len(tools)}


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def get_stats():
    """Get MCP registry statistics."""
    return registry.get_stats()
