"""MCP server – exposes research tools over the Model Context Protocol.

Run with:
    python -m mcp_server.server

The server listens on stdio (MCP default transport) or HTTP SSE when
MCP_TRANSPORT=sse is set. In docker-compose we use SSE so the agent container
can reach it over the network.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mcp_server.tools.execute_python import execute_python
from mcp_server.tools.fetch_url import fetch_url
from mcp_server.tools.read_pdf import read_pdf
from mcp_server.tools.search_kb import search_knowledge_base
from mcp_server.tools.web_search import web_search

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="web_search",
        description="Search the web for current information. Returns [{title, url, snippet}].",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (1-10)",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="fetch_url",
        description="Fetch a URL and return its main text content (boilerplate stripped). Max 500 KB.",  # noqa: E501
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
            },
            "required": ["url"],
        },
    ),
    Tool(
        name="read_pdf",
        description="Extract text from a PDF file (local path or URL). Returns text + page count.",
        inputSchema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Local file path or HTTP(S) URL to a PDF",
                },
            },
            "required": ["source"],
        },
    ),
    Tool(
        name="execute_python",
        description=(
            "Execute a Python code snippet in an isolated Docker container with no network access. "
            "Returns stdout, stderr, exit_code. Use for calculations, data processing, charting."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source code to execute"},
            },
            "required": ["code"],
        },
    ),
    Tool(
        name="search_knowledge_base",
        description="Search the local knowledge base for relevant passages. Returns top-k matches.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


async def _dispatch(name: str, args: dict) -> dict:  # type: ignore[type-arg]
    t0 = time.monotonic()
    log.info("tool.call", tool=name, args=args)

    match name:
        case "web_search":
            result = await web_search(
                query=args["query"],
                max_results=args.get("max_results", 5),
            )
        case "fetch_url":
            result = await fetch_url(url=args["url"])
        case "read_pdf":
            result = await read_pdf(source=args["source"])
        case "execute_python":
            result = await execute_python(code=args["code"])
        case "search_knowledge_base":
            result = await search_knowledge_base(
                query=args["query"],
                top_k=args.get("top_k", 5),
            )
        case _:
            raise ValueError(f"Unknown tool: {name}")

    latency_ms = int((time.monotonic() - t0) * 1000)
    log.info("tool.done", tool=name, latency_ms=latency_ms)
    return result


# ---------------------------------------------------------------------------
# MCP server wiring
# ---------------------------------------------------------------------------


def build_server() -> Server:
    server = Server("research-tools")

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        import json

        result = await _dispatch(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    return server


async def main() -> None:
    import structlog

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )

    server = build_server()
    transport = os.environ.get("MCP_TRANSPORT", "stdio")

    if transport == "stdio":
        log.info("mcp_server.start", transport="stdio")
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    else:
        raise ValueError(f"Unsupported MCP_TRANSPORT: {transport}")


if __name__ == "__main__":
    asyncio.run(main())
