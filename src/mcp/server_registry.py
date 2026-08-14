"""MCP Server Registry — stores MCP server configs, tools, and connection state.

Uses SQLite for persistence. Each server entry stores:
- id, name, description, url (stdio://, sse://, http://)
- transport type (stdio, sse, http)
- connected status, tools list, config
"""

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
import os


@dataclass
class McpTool:
    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class McpServer:
    id: str
    name: str
    url: str
    description: str = ""
    transport: str = "stdio"  # stdio | sse | http
    connected: bool = False
    enabled: bool = True
    tools: list[McpTool] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    created_at: float = 0.0
    last_connected: float | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "description": self.description,
            "transport": self.transport,
            "connected": self.connected,
            "enabled": self.enabled,
            "tools": [t.to_dict() for t in self.tools],
            "config": self.config,
            "created_at": self.created_at,
            "last_connected": self.last_connected,
            "error": self.error,
        }


class McpServerRegistry:
    """Manages MCP server registrations, connections, and tool discovery."""

    def __init__(self, db_path: str | None = None):
        db = db_path or os.path.expanduser("~/opensoul/data/mcp/mcp.db")
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_db()
        self._seed_defaults()

    def _init_db(self):
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS mcp_servers (
                id            TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                url           TEXT NOT NULL,
                description   TEXT DEFAULT '',
                transport     TEXT DEFAULT 'stdio',
                connected     INTEGER DEFAULT 0,
                enabled       INTEGER DEFAULT 1,
                tools_json    TEXT DEFAULT '[]',
                config_json   TEXT DEFAULT '{}',
                created_at    REAL NOT NULL,
                last_connected REAL,
                error         TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_mcp_name ON mcp_servers(name);
        """)
        self._db.commit()

    def _seed_defaults(self):
        """Seed well-known MCP servers if table is empty."""
        count = self._db.execute("SELECT COUNT(*) FROM mcp_servers").fetchone()[0]
        if count > 0:
            return

        defaults = [
            McpServer(
                id="mcp-filesystem", name="Filesystem",
                url="stdio://mcp-filesystem",
                description="Read, write, and manage files on the local filesystem.",
                transport="stdio", connected=False, enabled=True,
                tools=[
                    McpTool("read_file", "Read contents of a file", {"type": "object", "properties": {"path": {"type": "string"}}}),
                    McpTool("write_file", "Write contents to a file", {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}),
                    McpTool("list_directory", "List files in a directory", {"type": "object", "properties": {"path": {"type": "string"}}}),
                    McpTool("search_files", "Search for files by pattern", {"type": "object", "properties": {"pattern": {"type": "string"}}}),
                ],
            ),
            McpServer(
                id="mcp-github", name="GitHub",
                url="stdio://mcp-github",
                description="Interact with GitHub repositories, issues, and pull requests.",
                transport="stdio", connected=False, enabled=True,
                tools=[
                    McpTool("search_repos", "Search GitHub repositories"),
                    McpTool("list_issues", "List issues in a repository"),
                    McpTool("create_pr", "Create a pull request"),
                ],
            ),
            McpServer(
                id="mcp-brave-search", name="Brave Search",
                url="stdio://mcp-brave-search",
                description="Web and local search using the Brave Search API.",
                transport="stdio", connected=False, enabled=True,
                tools=[
                    McpTool("web_search", "Search the web"),
                    McpTool("local_search", "Search for local businesses"),
                ],
            ),
            McpServer(
                id="mcp-postgres", name="PostgreSQL",
                url="stdio://mcp-postgres",
                description="Query and manage PostgreSQL databases.",
                transport="stdio", connected=False, enabled=True,
                tools=[
                    McpTool("query", "Execute a SQL query"),
                    McpTool("list_tables", "List all tables in the database"),
                    McpTool("describe_table", "Show table schema"),
                ],
            ),
            McpServer(
                id="mcp-memory", name="Memory",
                url="stdio://mcp-memory",
                description="Persistent knowledge graph for long-term memory.",
                transport="stdio", connected=False, enabled=True,
                tools=[
                    McpTool("create_entities", "Create new entities in the knowledge graph"),
                    McpTool("search_nodes", "Search for nodes by query"),
                    McpTool("open_nodes", "Open specific nodes by name"),
                ],
            ),
        ]

        now = time.time()
        for srv in defaults:
            srv.created_at = now
            self._insert(srv)

    def _insert(self, srv: McpServer):
        self._db.execute(
            """INSERT OR IGNORE INTO mcp_servers
               (id, name, url, description, transport, connected, enabled,
                tools_json, config_json, created_at, last_connected, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (srv.id, srv.name, srv.url, srv.description, srv.transport,
             1 if srv.connected else 0, 1 if srv.enabled else 0,
             json.dumps([t.to_dict() for t in srv.tools]),
             json.dumps(srv.config),
             srv.created_at, srv.last_connected, srv.error),
        )
        self._db.commit()

    def _row_to_server(self, row) -> McpServer:
        d = dict(row)
        tools_raw = json.loads(d.get("tools_json") or "[]")
        tools = [McpTool(t.get("name", ""), t.get("description", ""), t.get("inputSchema", {})) for t in tools_raw]
        return McpServer(
            id=d["id"], name=d["name"], url=d["url"],
            description=d.get("description", ""),
            transport=d.get("transport", "stdio"),
            connected=bool(d.get("connected")),
            enabled=bool(d.get("enabled")),
            tools=tools,
            config=json.loads(d.get("config_json") or "{}"),
            created_at=d.get("created_at", 0),
            last_connected=d.get("last_connected"),
            error=d.get("error"),
        )

    # ── CRUD ──────────────────────────────────────────────────

    def list_servers(self, enabled_only: bool = False) -> list[dict]:
        if enabled_only:
            rows = self._db.execute("SELECT * FROM mcp_servers WHERE enabled = 1 ORDER BY name").fetchall()
        else:
            rows = self._db.execute("SELECT * FROM mcp_servers ORDER BY name").fetchall()
        return [self._row_to_server(r).to_dict() for r in rows]

    def get_server(self, server_id: str) -> McpServer | None:
        row = self._db.execute("SELECT * FROM mcp_servers WHERE id = ?", (server_id,)).fetchone()
        return self._row_to_server(row) if row else None

    def add_server(self, name: str, url: str, description: str = "",
                   transport: str = "stdio", config: dict | None = None,
                   tools: list[dict] | None = None) -> McpServer:
        srv_id = f"mcp-{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}"
        now = time.time()
        tools_list = [McpTool(t.get("name", ""), t.get("description", ""), t.get("inputSchema", {})) for t in (tools or [])]
        srv = McpServer(
            id=srv_id, name=name, url=url, description=description,
            transport=transport, config=config or {}, tools=tools_list,
            created_at=now,
        )
        self._insert(srv)
        return srv

    def update_server(self, server_id: str, **kwargs) -> McpServer | None:
        srv = self.get_server(server_id)
        if not srv:
            return None

        allowed = {"name", "url", "description", "transport", "enabled", "config", "tools"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}

        if not updates:
            return srv

        set_parts = []
        values = []
        for k, v in updates.items():
            if k == "config":
                set_parts.append("config_json = ?")
                values.append(json.dumps(v))
            elif k == "tools":
                set_parts.append("tools_json = ?")
                tools_list = [McpTool(t.get("name", ""), t.get("description", ""), t.get("inputSchema", {})) if isinstance(t, dict) else t for t in v]
                values.append(json.dumps([t.to_dict() for t in tools_list]))
            elif k == "enabled":
                set_parts.append("enabled = ?")
                values.append(1 if v else 0)
            else:
                set_parts.append(f"{k} = ?")
                values.append(v)

        values.append(server_id)
        self._db.execute(f"UPDATE mcp_servers SET {', '.join(set_parts)} WHERE id = ?", values)
        self._db.commit()
        return self.get_server(server_id)

    def delete_server(self, server_id: str) -> bool:
        result = self._db.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
        self._db.commit()
        return result.rowcount > 0

    # ── Connection ────────────────────────────────────────────

    def connect(self, server_id: str) -> dict:
        """Simulate connecting to an MCP server. In production, this would
        establish a real stdio/SSE/HTTP connection and discover tools."""
        srv = self.get_server(server_id)
        if not srv:
            return {"success": False, "error": "Server not found"}

        now = time.time()
        self._db.execute(
            "UPDATE mcp_servers SET connected = 1, last_connected = ?, error = NULL WHERE id = ?",
            (now, server_id),
        )
        self._db.commit()
        return {"success": True, "server_id": server_id, "connected": True}

    def disconnect(self, server_id: str) -> dict:
        srv = self.get_server(server_id)
        if not srv:
            return {"success": False, "error": "Server not found"}

        self._db.execute(
            "UPDATE mcp_servers SET connected = 0 WHERE id = ?", (server_id,)
        )
        self._db.commit()
        return {"success": True, "server_id": server_id, "connected": False}

    # ── Tools ─────────────────────────────────────────────────

    def list_tools(self, server_id: str | None = None) -> list[dict]:
        """List all tools, optionally filtered by server."""
        if server_id:
            srv = self.get_server(server_id)
            if not srv:
                return []
            return [{"server_id": srv.id, "server_name": srv.name, **t.to_dict()} for t in srv.tools]

        all_tools = []
        for srv_dict in self.list_servers(enabled_only=True):
            for t in srv_dict.get("tools", []):
                all_tools.append({"server_id": srv_dict["id"], "server_name": srv_dict["name"], **t})
        return all_tools

    def get_stats(self) -> dict:
        """Return summary stats."""
        servers = self.list_servers()
        total = len(servers)
        connected = sum(1 for s in servers if s["connected"])
        total_tools = sum(len(s.get("tools", [])) for s in servers)
        return {
            "total_servers": total,
            "connected": connected,
            "disconnected": total - connected,
            "total_tools": total_tools,
        }
