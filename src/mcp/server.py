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
from src.models.knowledge import KnowledgeCreate
from src.services import knowledge as knowledge_service
from src.services.rag import rag_query
from src.services.search import hybrid_search

server = MCPServer("opensoul") if MCP_AVAILABLE else None


if MCP_AVAILABLE:

    @server.tool()
    async def remember(title: str, content: str, user_id: str, tags: list[str] | None = None) -> str:
        """Store a new piece of knowledge into long-term memory."""
        from uuid import UUID

        uid = UUID(user_id)
        data = KnowledgeCreate(title=title, content=content, tags=tags or [])
        row = await knowledge_service.create_knowledge(data, uid)
        return json.dumps({"status": "remembered", "id": str(row["id"])})

    @server.tool()
    async def recall(query: str, user_id: str, top_k: int = 5) -> str:
        """Search and retrieve relevant memories using semantic search."""
        from uuid import UUID

        uid = UUID(user_id)
        results = await hybrid_search(query, uid, top_k)
        return json.dumps(results, default=str)

    @server.tool()
    async def ask(question: str, user_id: str, top_k: int = 5) -> str:
        """Ask a question and get an answer based on stored knowledge (RAG)."""
        from uuid import UUID

        uid = UUID(user_id)
        result = await rag_query(question, uid, top_k)
        return json.dumps(result, default=str)

    @server.tool()
    async def search(query: str, user_id: str, limit: int = 10) -> str:
        """Hybrid search combining semantic and full-text search."""
        from uuid import UUID

        uid = UUID(user_id)
        results = await hybrid_search(query, uid, limit)
        return json.dumps(results, default=str)

    @server.tool()
    async def list_memories(user_id: str, offset: int = 0, limit: int = 20) -> str:
        """List all stored memories for a user."""
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
