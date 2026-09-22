"""OpenSoul MCP Server — stdio transport for AI Agent integration."""

import asyncio
import json
import logging
import sys

logger = logging.getLogger(__name__)

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.types import TextContent

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    MCPServer = None
    TextContent = None
    logger.warning("mcp not installed — MCP server disabled")

from src.database.meilisearch import meili_client
from src.database.postgres import db_pool
from src.database.qdrant import qdrant_client
from src.mcp.session_grants import check_tool_scope, published_tool_scope
from src.models.knowledge import KnowledgeCreate
from src.services import knowledge as knowledge_service
from src.services.rag import rag_query
from src.services.search import hybrid_search

server = MCPServer("opensoul") if MCP_AVAILABLE else None


def _require_auth(auth_token: str | None, tool_name: str) -> str | None:
    """发布侧auth（SUMMARY.md §五 mcp行）：返回None=通过，否则返回错误文本。

    fail-closed：缺token/未知token/已吊销/scope不含该工具 → 拒绝执行，
    拒绝带明确reason（mem0 §1.1 失败可见，禁止静默放行）。
    """
    ok, reason = check_tool_scope(auth_token, published_tool_scope(tool_name))
    return None if ok else reason


if MCP_AVAILABLE:

    @server.tool()
    async def remember(
        title: str,
        content: str,
        user_id: str,
        tags: list[str] | None = None,
        auth_token: str | None = None,
    ) -> str:
        """Store a new piece of knowledge into long-term memory."""
        if err := _require_auth(auth_token, "remember"):
            return err
        from uuid import UUID

        uid = UUID(user_id)
        data = KnowledgeCreate(title=title, content=content, tags=tags or [])
        row = await knowledge_service.create_knowledge(data, uid)
        return json.dumps({"status": "remembered", "id": str(row["id"])})

    @server.tool()
    async def recall(
        query: str, user_id: str, top_k: int = 5, auth_token: str | None = None
    ) -> str:
        """Search and retrieve relevant memories using semantic search."""
        if err := _require_auth(auth_token, "recall"):
            return err
        from uuid import UUID

        uid = UUID(user_id)
        results = await hybrid_search(query, uid, top_k)
        return json.dumps(results, default=str)

    @server.tool()
    async def ask(
        question: str, user_id: str, top_k: int = 5, auth_token: str | None = None
    ) -> str:
        """Ask a question and get an answer based on stored knowledge (RAG)."""
        if err := _require_auth(auth_token, "ask"):
            return err
        from uuid import UUID

        uid = UUID(user_id)
        result = await rag_query(question, uid, top_k)
        return json.dumps(result, default=str)

    @server.tool()
    async def search(
        query: str, user_id: str, limit: int = 10, auth_token: str | None = None
    ) -> str:
        """Hybrid search combining semantic and full-text search."""
        if err := _require_auth(auth_token, "search"):
            return err
        from uuid import UUID

        uid = UUID(user_id)
        results = await hybrid_search(query, uid, limit)
        return json.dumps(results, default=str)

    @server.tool()
    async def list_memories(
        user_id: str, offset: int = 0, limit: int = 20, auth_token: str | None = None
    ) -> str:
        """List all stored memories for a user."""
        if err := _require_auth(auth_token, "list_memories"):
            return err
        from uuid import UUID

        uid = UUID(user_id)
        rows = await knowledge_service.list_knowledge(uid, offset, limit)
        return json.dumps(rows, default=str)


async def main():
    if not MCP_AVAILABLE:
        logger.error("Cannot start MCP server: mcp package not installed")
        sys.exit(1)
    await db_pool.connect()
    qdrant_client.ensure_collection()
    meili_client.ensure_index()
    await server.run("stdio")
    await db_pool.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
