"""OpenSoul MCP Server — stdio transport for AI Agent integration."""

import asyncio
import json
import logging
import sys

logger = logging.getLogger(__name__)

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    Server = None
    stdio_server = None
    Tool = None
    TextContent = None
    logger.warning("mcp not installed — MCP server disabled")

from src.database.postgres import db_pool
from src.database.qdrant import qdrant_client
from src.database.meilisearch import meili_client
from src.services import knowledge as knowledge_service
from src.services.search import hybrid_search
from src.services.rag import rag_query
from src.models.knowledge import KnowledgeCreate

server = Server("opensoul") if MCP_AVAILABLE else None


if MCP_AVAILABLE:

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="remember",
                description="Store a new piece of knowledge into long-term memory",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Title of the knowledge"},
                        "content": {"type": "string", "description": "Content to remember"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
                        "user_id": {"type": "string", "description": "User UUID"},
                    },
                    "required": ["title", "content", "user_id"],
                },
            ),
            Tool(
                name="recall",
                description="Search and retrieve relevant memories using semantic search",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to search for"},
                        "user_id": {"type": "string", "description": "User UUID"},
                        "top_k": {"type": "integer", "description": "Number of results", "default": 5},
                    },
                    "required": ["query", "user_id"],
                },
            ),
            Tool(
                name="ask",
                description="Ask a question and get an answer based on stored knowledge (RAG)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The question to answer"},
                        "user_id": {"type": "string", "description": "User UUID"},
                        "top_k": {"type": "integer", "description": "Number of context chunks", "default": 5},
                    },
                    "required": ["question", "user_id"],
                },
            ),
            Tool(
                name="search",
                description="Hybrid search combining semantic and full-text search",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "user_id": {"type": "string", "description": "User UUID"},
                        "limit": {"type": "integer", "description": "Max results", "default": 10},
                    },
                    "required": ["query", "user_id"],
                },
            ),
            Tool(
                name="list_memories",
                description="List all stored memories for a user",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "User UUID"},
                        "offset": {"type": "integer", "default": 0},
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": ["user_id"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        from uuid import UUID

        user_id = UUID(arguments["user_id"])

        if name == "remember":
            data = KnowledgeCreate(
                title=arguments["title"],
                content=arguments["content"],
                tags=arguments.get("tags", []),
            )
            row = await knowledge_service.create_knowledge(data, user_id)
            return [TextContent(type="text", text=json.dumps({"status": "remembered", "id": str(row["id"])}))]

        elif name == "recall":
            results = await hybrid_search(arguments["query"], user_id, arguments.get("top_k", 5))
            return [TextContent(type="text", text=json.dumps(results, default=str))]

        elif name == "ask":
            result = await rag_query(arguments["question"], user_id, arguments.get("top_k", 5))
            return [TextContent(type="text", text=json.dumps(result, default=str))]

        elif name == "search":
            results = await hybrid_search(arguments["query"], user_id, arguments.get("limit", 10))
            return [TextContent(type="text", text=json.dumps(results, default=str))]

        elif name == "list_memories":
            rows = await knowledge_service.list_knowledge(user_id, arguments.get("offset", 0), arguments.get("limit", 20))
            return [TextContent(type="text", text=json.dumps(rows, default=str))]

        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


async def main():
    if not MCP_AVAILABLE:
        logger.error("Cannot start MCP server: mcp package not installed")
        sys.exit(1)
    await db_pool.connect()
    qdrant_client.ensure_collection()
    meili_client.ensure_index()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
    await db_pool.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
